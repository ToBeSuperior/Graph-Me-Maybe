"""What the server keeps on disk and in memory is let go once nothing needs it: crops no node
names any more (a document deleted, read again, a reading that failed long ago), and documents
held in memory once they are in the graph. Invented files only."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from graphme.adapters.storage.assets import LocalAssetStore
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.config import Settings
from graphme.server.jobs import CorpusBuild
from graphme.server.state import Demo
from tests.fakes.documents import named_document
from tests.public.server.conftest import FakeSpace, until


def _aged(store: LocalAssetStore, content: bytes, *, hours: float = 2) -> str:
    digest = store.put(content, suffix=".png")
    path = store.path_for(digest, ".png")
    then = time.time() - hours * 3600
    os.utime(path, (then, then))
    return digest


def test_only_the_assets_no_node_names_are_deleted_and_never_one_just_written(
    tmp_path: Path,
) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    named, orphan = _aged(store, b"named crop"), _aged(store, b"orphan crop")
    fresh = store.put(b"written by a reading under way", suffix=".png")

    removed, size = store.sweep({named}, before=time.time() - 600)

    assert (removed, size) == (1, len(b"orphan crop"))
    assert store.path_for(named, ".png").exists() and store.path_for(fresh, ".png").exists()
    assert not store.path_for(orphan, ".png").exists()
    assert not store.path_for(orphan, ".png").parent.exists()  # an emptied folder goes too


def test_a_workspace_keeps_its_crops_beside_its_graph(tmp_path: Path) -> None:
    home = Settings(profile="test", embedding_provider="hash", data_dir=tmp_path / "data")
    other = home.model_copy(update={"local_graph": tmp_path / "workspaces" / "w1"})

    assert home.asset_dir == tmp_path / "data" / "assets"
    assert other.asset_dir == tmp_path / "workspaces" / "w1" / "assets"


def _demo_store(demo: Demo) -> LocalAssetStore:
    # The shared test settings keep data in the working directory's `.graphme`: a sweep there
    # would delete a checkout's own crops.
    demo.settings = demo.settings.model_copy(update={"data_dir": demo.root / "data"})
    return LocalAssetStore(demo.settings.asset_dir)


def test_a_sweep_keeps_what_the_graph_and_a_reading_outside_it_name(
    demo: Demo, space: FakeSpace
) -> None:
    store = _demo_store(demo)
    in_graph, outside, gone = (_aged(store, b"in graph"), _aged(store, b"read, not put"),
                               _aged(store, b"deleted document"))
    space.crops = {in_graph}
    # A reading that failed to go in keeps its crops: "Read again" puts it in at no cost.
    doc = demo.add("invented.pdf", b"%PDF-1.4 invented")
    session = demo.session(doc)
    extracted = named_document("b", "invented.pdf")
    node = extracted.nodes[0].model_copy(update={"metadata": {CROP_ASSET_KEY: outside}})
    session.extracted = extracted.model_copy(update={"nodes": (node, *extracted.nodes[1:])})
    session.state = "done"

    demo.sweep_assets()

    assert store.path_for(in_graph, ".png").exists()
    assert store.path_for(outside, ".png").exists()
    assert not store.path_for(gone, ".png").exists()


def test_nothing_is_swept_while_a_build_runs(demo: Demo, space: FakeSpace) -> None:
    store = _demo_store(demo)
    orphan = _aged(store, b"maybe a build's own")
    demo.build = CorpusBuild(id="b", state="reading")

    demo.sweep_assets()

    assert store.path_for(orphan, ".png").exists()


def test_a_deleted_document_s_crops_are_swept_behind(
    demo: Demo, space: FakeSpace, client: Any
) -> None:
    store = _demo_store(demo)
    crop = _aged(store, b"the deleted document's crop")
    space.held["d1"] = "memo.pdf"

    assert client.delete("/v1/documents/d1").status_code == 200

    assert until(lambda: not store.path_for(crop, ".png").exists())


def test_documents_put_in_the_graph_are_not_held_in_memory_after_the_build(
    demo: Demo, space: FakeSpace
) -> None:
    from tests.public.server.conftest import read_as

    demo._run = read_as("a", "memo.pdf")  # type: ignore[method-assign]
    doc = demo.add("memo.pdf", b"%PDF-1.4 invented")

    build = demo.build_corpus([doc], "test")

    assert until(lambda: build.state == "done"), build
    assert space.put == ["a"]
    # Held as the graph keeps it (where each node is drawn), or not at all -- never as read.
    assert until(lambda: doc not in demo.sessions or demo.sessions[doc].extracted is None)


def test_a_build_gives_back_to_the_cloud_only_what_was_online_only_before_it_read(
    demo: Demo, space: FakeSpace, monkeypatch: Any
) -> None:
    from graphme.server import state
    from tests.public.server.conftest import read_as

    online = demo.root / "online.pdf"
    kept = demo.root / "kept.pdf"
    for path in (online, kept):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 invented")
    monkeypatch.setattr(state, "online_only", lambda path: Path(path) == online)
    given_back: list[Path] = []
    monkeypatch.setattr(state, "evict", lambda paths: given_back.extend(paths) or len(paths))
    demo._run = read_as("a", "online.pdf")  # type: ignore[method-assign]

    build = demo.build_corpus([demo.open_path(str(online)), demo.open_path(str(kept))], "test")

    assert until(lambda: build.state == "done" and given_back), build
    assert given_back == [online]  # a file the user keeps here is left as it is

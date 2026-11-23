"""Questions about chosen files: a path becomes the document its content is in the graph."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.storage import local_neo4j
from graphme.server import create_app, state
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST
from tests.public.server.conftest import SETTINGS, FakeSpace
from tests.public.server.test_questions import FakeRag
from tests.public.server.test_several_workspaces import _Graph


def _file(folder: Path, name: str, body: bytes) -> Path:
    path = folder / name
    path.write_bytes(body)
    return path


def test_scope_of_a_file_in_the_graph_names_its_document(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    held = _file(tmp_path, "memo.docx", b"invented memo")
    other = _file(tmp_path, "spec.docx", b"invented spec")
    space.by_hash[state.content_hash(held) or ""] = "memo-id"

    said = client.post("/v1/scope", json={"paths": [str(held), str(tmp_path)]}).json()

    assert said["document_ids"] == ["memo-id"]
    assert said["missing"] == [str(other)]  # the folder walk also reached the new file
    assert said["workspace"] == demo.settings.workspace  # no registry: the open graph's id


def test_scope_of_a_new_file_is_missing(demo: Demo, client: TestClient, tmp_path: Path) -> None:
    new = _file(tmp_path, "draft.docx", b"invented draft")

    said = client.post("/v1/scope", json={"paths": [str(new), str(tmp_path / "gone.docx")]}).json()

    assert said["document_ids"] == [] and said["workspace"] is None
    assert demo.workspaces is None and said["build_into"] == demo.settings.workspace
    assert sorted(said["missing"]) == sorted([str(new), str(tmp_path / "gone.docx")])


def test_a_question_passes_its_documents_to_the_search(demo: Demo, client: TestClient) -> None:
    seen: list[Any] = []

    class Scoped(FakeRag):
        def find(self, question: str, *, documents: int, history: Any = (),
                 only: Any = None) -> list[Any]:
            seen.append(only)
            return []

    demo.graph._rag = Scoped()  # type: ignore[attr-defined]

    asked = client.post("/v1/search", json={"question": "invented", "only": ["gone"]})
    open_ = client.post("/v1/search", json={"question": "invented"})

    assert asked.status_code == 200 and asked.json()["sources"] == []
    assert open_.status_code == 200
    assert seen == [frozenset({"gone"}), None]


def test_only_naming_nothing_finds_nothing_and_none_means_every_document(
    demo: Demo, client: TestClient
) -> None:
    seen: list[Any] = []

    class Scoped(FakeRag):
        def find(self, question: str, *, documents: int, history: Any = (),
                 only: Any = None) -> list[Any]:
            seen.append(only)
            return []

    demo.graph._rag = Scoped()  # type: ignore[attr-defined]

    client.post("/v1/search", json={"question": "invented", "only": []})
    client.post("/v1/search", json={"question": "invented", "only": None})

    assert seen == [frozenset(), None]


# Several workspaces ------------------------------------------------------------------------


@pytest.fixture
def spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Demo, TestClient]:
    monkeypatch.setattr(state, "GraphMe", _Graph)
    monkeypatch.setattr(local_neo4j, "stop_graph", lambda graph: None)
    home = tmp_path / "home"
    demo = Demo(SETTINGS.model_copy(update={"local_neo4j": home, "workspace": FIRST}),
                renderer=None, root=home / "server")  # type: ignore[arg-type]
    return demo, TestClient(create_app(demo), base_url="http://127.0.0.1:8765")


def _holding(demo: Demo, workspace: str, *files: Path) -> None:
    space = demo.reader(workspace).graph_space()
    for f in files:
        space.by_hash[state.content_hash(f) or ""] = f"{workspace}:{f.name}"


def _made(demo: Demo, name: str, kind: str = "documents") -> str:
    return str(demo.workspaces.create(name, kind=kind)["id"])  # type: ignore[union-attr]


def test_a_tie_goes_to_the_open_workspace(
    spaces: tuple[Demo, TestClient], tmp_path: Path
) -> None:
    demo, client = spaces
    other = _made(demo, "Other")
    memo = _file(tmp_path, "memo.docx", b"invented memo")
    _holding(demo, other, memo)
    _holding(demo, FIRST, memo)

    said = client.post("/v1/scope", json={"paths": [str(memo)]}).json()

    assert said["workspace"] == FIRST and said["document_ids"] == [f"{FIRST}:memo.docx"]
    assert said["build_into"] == FIRST and said["missing"] == [] and said["elsewhere"] == []


def test_the_workspace_holding_most_wins_and_the_rest_are_elsewhere_not_missing(
    spaces: tuple[Demo, TestClient], tmp_path: Path
) -> None:
    demo, client = spaces
    other = _made(demo, "Other")
    one, two, three = (_file(tmp_path, f"{n}.docx", n.encode()) for n in ("a", "b", "c"))
    _holding(demo, other, one, two)
    _holding(demo, FIRST, three)
    new = _file(tmp_path, "new.docx", b"invented new")

    said = client.post("/v1/scope", json={"paths": [str(tmp_path)]}).json()

    assert said["workspace"] == other
    assert sorted(said["document_ids"]) == [f"{other}:a.docx", f"{other}:b.docx"]
    assert said["elsewhere"] == [{"path": str(three), "workspace": FIRST}]
    assert said["missing"] == [str(new)]
    assert said["build_into"] == other


def test_nothing_held_anywhere_is_null_and_builds_into_the_open_one(
    spaces: tuple[Demo, TestClient], tmp_path: Path
) -> None:
    demo, client = spaces
    _made(demo, "Other")
    new = _file(tmp_path, "new.docx", b"invented new")

    said = client.post("/v1/scope", json={"paths": [str(new), str(tmp_path)]}).json()

    assert said["workspace"] is None and said["document_ids"] == []
    assert said["missing"] == [str(new)]  # given twice, once
    assert said["build_into"] == FIRST


def test_a_mail_workspace_is_never_built_into(
    spaces: tuple[Demo, TestClient], tmp_path: Path
) -> None:
    demo, client = spaces
    mail = _made(demo, "Mailbox", kind="mail")
    attachment = _file(tmp_path, "att.docx", b"invented attachment")
    _holding(demo, mail, attachment)
    new = _file(tmp_path, "new.docx", b"invented new")

    said = client.post("/v1/scope", json={"paths": [str(attachment), str(new)]}).json()

    assert said["workspace"] == mail and said["missing"] == [str(new)]
    assert said["elsewhere"] == []
    assert said["build_into"] == FIRST  # the open one, not the mail workspace


def test_a_file_held_in_two_other_workspaces_names_the_first_listed(
    spaces: tuple[Demo, TestClient], tmp_path: Path
) -> None:
    demo, client = spaces
    first, second = _made(demo, "First"), _made(demo, "Second")
    both = _file(tmp_path, "both.docx", b"invented both")
    only_one = _file(tmp_path, "mine.docx", b"invented mine")
    _holding(demo, first, both)
    _holding(demo, second, both)
    _holding(demo, FIRST, only_one, _file(tmp_path, "x.docx", b"x"))

    said = client.post("/v1/scope", json={"paths": [str(both), str(only_one)]}).json()

    assert said["workspace"] == FIRST
    assert said["elsewhere"] == [{"path": str(both), "workspace": first}]


def test_a_scope_of_too_many_files_is_turned_away_before_any_is_read(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(3):
        _file(tmp_path, f"invented-{n}.docx", b"invented %d" % n)
    _cap(monkeypatch, 2)
    read: list[Path] = []
    monkeypatch.setattr(state, "content_hash", lambda path: read.append(path))

    said = client.post("/v1/scope", json={"paths": [str(tmp_path)]})

    assert said.status_code == 400
    assert "more than 2 files" in said.json()["detail"] and "fewer" in said.json()["detail"]
    assert read == []


def _cap(monkeypatch: pytest.MonkeyPatch, most: int) -> None:
    from graphme.server import space_jobs

    monkeypatch.setattr(space_jobs, "MAX_SCOPE_FILES", most)


def test_a_scope_stops_walking_once_it_holds_too_many_files(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graphme.application import ingest

    for name in ("a", "b", "c", "d", "e", "f"):
        (tmp_path / name).mkdir()
        _file(tmp_path / name, "invented.docx", name.encode())
    _cap(monkeypatch, 1)
    listed: list[Path] = []
    real = ingest._listed

    def counted(here: Path, *args: Any) -> Any:
        listed.append(here)
        return real(here, *args)

    monkeypatch.setattr(ingest, "_listed", counted)

    said = client.post("/v1/scope", json={"paths": [str(tmp_path)]})

    assert said.status_code == 400
    assert len(listed) < 7  # the walk stopped: not every one of the seven folders was listed


def test_a_file_kept_online_only_is_given_back_once_its_content_is_known(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clouded = _file(tmp_path, "cloud.docx", b"invented cloud memo")
    local = _file(tmp_path, "local.docx", b"invented local memo")
    space.by_hash[state.content_hash(clouded) or ""] = "cloud-id"
    monkeypatch.setattr(state, "online_only", lambda path: Path(path) == clouded)
    given: list[Path] = []
    monkeypatch.setattr(state, "evict", lambda paths: given.extend(paths) or len(paths))

    said = client.post("/v1/scope", json={"paths": [str(clouded), str(local)]}).json()

    assert said["document_ids"] == ["cloud-id"]
    assert given == [clouded]  # the one downloaded to be hashed, and only it


def test_another_sites_page_cannot_ask_for_a_scope(demo: Demo, tmp_path: Path) -> None:
    memo = _file(tmp_path, "memo.docx", b"invented memo")
    demo.settings = demo.settings.model_copy(update={"server_key": "invented-server-key"})
    key = {"Authorization": "Bearer invented-server-key"}
    foreign = TestClient(create_app(demo), base_url="http://elsewhere.invented")
    local = TestClient(create_app(demo), base_url="http://127.0.0.1:8765")

    # the key is no licence from another site: a scope reads this machine's files
    told = foreign.post("/v1/scope", json={"paths": [str(memo)]}, headers=key)
    page = local.post("/v1/scope", json={"paths": [str(memo)]},
                      headers={**key, "origin": "https://elsewhere.invented"})
    ours = local.post("/v1/scope", json={"paths": [str(memo)]}, headers=key)

    assert told.status_code == 403 and page.status_code == 403
    assert ours.status_code == 200


def test_a_scope_is_found_without_the_server(
    demo: Demo, space: FakeSpace, tmp_path: Path
) -> None:
    held = _file(tmp_path, "memo.docx", b"invented memo")
    space.by_hash[state.content_hash(held) or ""] = "memo-id"

    said = demo.scope_of([str(held), str(tmp_path / "gone.docx")],
                         space_of=lambda workspace: space)

    assert said["document_ids"] == ["memo-id"]
    assert said["missing"] == [str(tmp_path / "gone.docx")]

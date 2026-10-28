"""A build from a cloud drive downloads no more than it reads at once and as many again waiting
their turn: a whole folder fetched ahead filled the disk faster than it was read. Each file is
given back to the cloud as soon as it is read. Invented files only."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from graphme.server import state
from graphme.server.state import Demo
from tests.fakes.documents import named_document
from tests.public.server.conftest import FakeSpace, until


def test_a_build_keeps_no_more_downloaded_than_it_reads_and_queues(
    demo: Demo, space: FakeSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = []
    for n in range(10):
        path = demo.root / "drive" / f"memo{n}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"%PDF-1.4 invented {n}".encode())
        files.append(path)
    here: set[Path] = set()  # downloaded and not given back yet
    peak = [0]
    lock = threading.Lock()

    def fetched(path: Path) -> None:
        with lock:
            here.add(Path(path))
            peak[0] = max(peak[0], len(here))

    real_hash = state.content_hash

    def hashing(path: Path) -> Any:
        fetched(path)  # reading the content downloads it
        return real_hash(path)

    def given_back(paths: Any) -> int:
        with lock:
            here.difference_update(Path(p) for p in paths)
        return len(list(paths))

    monkeypatch.setattr(state, "content_hash", hashing)
    monkeypatch.setattr(state, "online_only", lambda path: True)
    monkeypatch.setattr(state, "evict", given_back)
    monkeypatch.setattr(state, "embedded_in", lambda path: [])

    def read(session: Any) -> None:
        fetched(session.source)
        time.sleep(0.05)
        session.extracted = named_document(f"{int(session.source.stem[4:]) + 1:x}",
                                           session.source.name)
        session.state = "done"

    demo._run = read  # type: ignore[method-assign]
    demo.readers = ThreadPoolExecutor(max_workers=2)
    demo.reading_at_once = 2

    build = demo.build_corpus([demo.open_path(str(f)) for f in files], "test")

    assert until(lambda: build.state == "done", 10), build
    assert len(space.put) == 10 and build.documents == 10
    assert peak[0] <= 4  # two read at once, two waiting -- never the folder
    assert until(lambda: not here)  # every one given back


def test_a_file_downloaded_again_after_it_was_given_back_is_given_back_again(
    demo: Demo, space: FakeSpace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # macOS's Spotlight indexes a file a build downloaded, reading it -- after the build gave it
    # back -- and the drive downloads it again. Once indexed it is left alone.
    path = demo.root / "drive" / "memo.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 invented")
    online = {path: True}
    indexed: list[Path] = []

    def given_back(paths: Any) -> int:
        for p in paths:
            online[Path(p)] = True
            if not indexed:  # the indexer reads it once, a moment later
                indexed.append(Path(p))
                threading.Timer(0.05, lambda: online.__setitem__(path, False)).start()
        return 1

    monkeypatch.setattr(state, "online_only", lambda p: online.get(Path(p), False))
    monkeypatch.setattr(state, "evict", given_back)
    monkeypatch.setattr(state, "embedded_in", lambda p: [])
    monkeypatch.setattr(state, "SETTLE_SECONDS", (0.2, 0.4))

    def read(session: Any) -> None:
        online[Path(session.source)] = False  # read: downloaded
        session.extracted = named_document("a", session.source.name)
        session.state = "done"

    demo._run = read  # type: ignore[method-assign]
    build = demo.build_corpus([demo.open_path(str(path))], "test")

    assert until(lambda: build.state == "done"), build
    assert until(lambda: bool(indexed))
    time.sleep(0.6)  # past the indexer's read and the checks after the build
    assert online[path]  # given back again, for good

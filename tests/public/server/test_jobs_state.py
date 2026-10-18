"""Jobs: every build, reading, embedding and export is kept by its id; two slots, one job in
each at a time."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from graphme.server import state
from graphme.server.state import Demo
from tests.public.server.conftest import FakeSpace, read_as, until


class FakeRag:
    """Embeds when let: `release` holds it, so a test sees it under way."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.embedded = 0

    def embed(self, *, on_progress: Any = None) -> int:
        self.release.wait(5)
        self.embedded += 1
        return 3


def _file(demo: Demo, tmp_path: Path, name: str = "a.docx") -> str:
    (tmp_path / name).write_bytes(name.encode())
    return demo.open_path(str(tmp_path / name))


def test_every_job_is_kept_by_its_id_with_what_it_is(demo: Demo, tmp_path: Path) -> None:
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    doc = _file(demo, tmp_path)

    first = demo.build_corpus([doc], "")
    assert until(lambda: first.state == "done")
    second = demo.build_corpus([doc], "", put=False)
    exported = demo.start_export("dump", None)

    assert first.id and second.id and exported.id and len({first.id, second.id, exported.id}) == 3
    assert list(demo.jobs.values()) == [first, second, exported]
    assert (first.kind, second.kind, exported.kind) == ("build", "read", "dump")


def test_a_read_job_reads_and_puts_nothing_in_the_space(
    demo: Demo, space: FakeSpace, tmp_path: Path
) -> None:
    """A file read on its own, the way a build reads it, stopped before the graph space."""
    ran: list[str] = []

    def run(session: Any) -> None:
        ran.append(session.source.name)
        read_as("a", "a.docx")(session)

    demo._run = run  # type: ignore[method-assign]
    job = demo.build_corpus([_file(demo, tmp_path)], "", put=False)

    assert until(lambda: job.state == "done"), job
    assert ran == ["a.docx"] and job.documents == 1
    assert space.put == []


def test_an_embedding_takes_the_builds_slot(demo: Demo, tmp_path: Path) -> None:
    rag = FakeRag()
    demo.graph._rag = rag  # type: ignore[assignment]

    job = demo.start_embed()
    assert job.kind == "embed" and job.state == "embedding"
    with pytest.raises(state.Busy):
        demo.build_corpus([_file(demo, tmp_path)], "")
    with pytest.raises(state.Busy):
        demo.start_embed()
    rag.release.set()

    assert until(lambda: job.state == "done"), job
    assert job.embedded == 3 and rag.embedded == 1


def test_a_build_embeds_only_when_asked_and_a_key_is_set(demo: Demo, tmp_path: Path) -> None:
    rag = FakeRag()
    rag.release.set()
    demo.graph._rag = rag  # type: ignore[assignment]
    demo.settings = demo.settings.model_copy(update={"embedding_key": "invented-key"})
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    doc = _file(demo, tmp_path)

    plain = demo.build_corpus([doc], "", embed=False)
    assert until(lambda: plain.state == "done") and rag.embedded == 0
    demo.drop([doc])
    doc = _file(demo, tmp_path, "b.docx")
    embedded = demo.build_corpus([doc], "")
    assert until(lambda: embedded.state == "done") and rag.embedded == 1


def test_only_a_build_or_a_reading_is_cancelled(demo: Demo, tmp_path: Path) -> None:
    rag = FakeRag()
    demo.graph._rag = rag  # type: ignore[assignment]
    embedding = demo.start_embed()
    with pytest.raises(state.Busy):
        demo.cancel(embedding)
    rag.release.set()
    assert until(lambda: embedding.state == "done")
    with pytest.raises(state.Busy):
        demo.cancel(demo.start_export("dump", None))

    release = threading.Event()
    demo._run = lambda session: release.wait(5)  # type: ignore[method-assign]
    build = demo.build_corpus([_file(demo, tmp_path)], "")
    assert demo.cancel(build) is build
    release.set()
    assert until(lambda: build.state == "cancelled"), build

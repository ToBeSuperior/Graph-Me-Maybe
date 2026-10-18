"""The server's state, without HTTP: reading, building, cancelling, and what it refuses."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.server import state
from graphme.server.state import Demo
from tests.fakes.documents import named_document as _doc
from tests.public.server.conftest import SETTINGS, FakeSpace, read_as, until


def test_a_document_whose_pages_were_only_drawn_is_still_read_by_a_build(
    demo: Demo, space: FakeSpace, tmp_path: Path
) -> None:
    """Clicking a document in the corpus view draws its pages and nothing else. A folder
    build that holds it must still read it -- it waited for a reading nobody started."""
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF-1.4")
    ran: list[str] = []

    def run(session: Any) -> None:
        ran.append(session.source.name)
        read_as("a", "memo.pdf")(session)

    demo._run = run  # type: ignore[method-assign]
    doc = demo.open_path(str(source))
    demo.session(doc).state = "rendered"  # drawn for the corpus view, never read

    build = demo.build_corpus([doc], "test")

    assert until(lambda: build.state == "done"), build
    assert ran == ["memo.pdf"]
    assert space.put == ["a"]


def test_a_document_chosen_twice_at_once_is_drawn_once(
    demo: Demo, space: FakeSpace, tmp_path: Path
) -> None:
    """Two clicks on one document in the map ask for its pages side by side; both get the
    same pages, drawn by one of them."""
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4")
    space.paths["doc-x"] = str(source)
    drawn: list[str] = []

    def prepare(session: Any) -> None:
        drawn.append(session.source.name)
        time.sleep(0.2)
        session.pages = ()

    demo._prepare = prepare  # type: ignore[method-assign]
    answers: list[str] = []
    clicks = [
        threading.Thread(target=lambda: answers.append(demo.pages_of_document("doc-x")))
        for _ in range(2)
    ]
    for click in clicks:
        click.start()
    for click in clicks:
        click.join()

    assert drawn == ["paper.pdf"]
    assert len(set(answers)) == 1


def test_a_document_being_read_is_not_drawn_again_for_its_pages(
    demo: Demo, space: FakeSpace, tmp_path: Path
) -> None:
    """Opened for its pages while a build reads it, it is handed back as it is: drawing it
    again here would replace what the reading holds."""
    original = tmp_path / "spec.docx"
    original.write_bytes(b"x")
    doc = demo.open_path(str(original))
    demo.session(doc).state = "reading"
    prepared: list[Any] = []
    demo._prepare = lambda session: prepared.append(session)  # type: ignore[method-assign]
    space.paths["s1"] = str(original)

    assert demo.pages_of_document("s1") == doc
    assert prepared == []


def test_a_cancel_stops_only_its_own_build(demo: Demo, tmp_path: Path) -> None:
    """A cancelled build leaves nothing behind: a document read afterwards -- uploaded on its
    own, or by the next build -- is read, not reported as cancelled."""
    path = tmp_path / "a.docx"
    path.write_bytes(b"x")
    doc = demo.open_path(str(path))
    stopped = threading.Event()
    stopped.set()
    session = demo.session(doc)
    session.stop = stopped
    demo._steps(session, lambda s: None)
    assert session.state == "cancelled" and session.error == state.CANCELLED

    ran: list[Any] = []
    demo.readers.submit = lambda fn, s: ran.append(s.stop)  # type: ignore[method-assign]
    demo.start(doc)
    assert ran == [None], "read on its own, it carries no build's stop"


def _held(demo: Demo, steps: list[tuple[str, str]]) -> tuple[threading.Event, threading.Event]:
    """`demo` reading one document at a time, its first drawing waiting for the gate."""
    demo.readers = ThreadPoolExecutor(max_workers=1)
    gate, entered = threading.Event(), threading.Event()

    def prepare(session: Any) -> None:
        steps.append(("prepare", session.source.name))
        entered.set()
        gate.wait(5)

    demo._prepare = prepare  # type: ignore[method-assign]
    demo._read = lambda session: steps.append(("read", session.source.name))  # type: ignore[method-assign]
    demo._enrich = lambda session: steps.append(("enrich", session.source.name))  # type: ignore[method-assign]
    return gate, entered


def test_a_cancel_passes_over_what_has_not_started_and_stops_the_rest_between_steps(
    demo: Demo, tmp_path: Path
) -> None:
    """Documents still waiting their turn are passed over the moment the build is cancelled,
    not left showing as being read; the one being read stops before its next step."""
    steps: list[tuple[str, str]] = []
    gate, entered = _held(demo, steps)
    docs = []
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.4 " + name.encode())
        docs.append(demo.open_path(str(tmp_path / name)))

    build = demo.build_corpus(docs, "test")
    assert entered.wait(5) and until(
        lambda: all(doc in demo.sessions and demo.sessions[doc].state == "rendering"
                    for doc in docs)
    )
    demo.cancel_build()

    waiting = [demo.sessions[doc] for doc in docs[1:]]
    assert [s.state for s in waiting] == ["cancelled", "cancelled"]
    assert all(s.error == state.CANCELLED for s in waiting)
    gate.set()
    assert until(lambda: build.state == "cancelled"), build
    assert steps == [("prepare", "a.pdf")]


def test_a_cancel_stops_a_document_its_upload_had_started_reading(demo: Demo) -> None:
    """A reading already under way when a build takes it in is stopped by that build's
    cancel."""
    steps: list[tuple[str, str]] = []
    gate, entered = _held(demo, steps)
    doc = demo.add("memo.pdf", b"%PDF-1.4 memo")
    demo.start(doc)
    assert entered.wait(5)

    build = demo.build_corpus([doc], "")
    demo.cancel_build()
    gate.set()

    assert until(lambda: build.state == "cancelled"), build
    assert until(lambda: demo.sessions[doc].state == "cancelled")
    assert steps == [("prepare", "memo.pdf")]
    assert demo.sessions[doc].error == state.CANCELLED


def test_a_document_that_fails_says_so_plainly_and_its_paths_stay_in_the_log(
    demo: Demo, space: FakeSpace, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What failed is said without this machine's paths; the whole of it is in the log."""
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF-1.4")

    def prepare(session: Any) -> None:
        raise RuntimeError(f"browser render failed for {session.source}: file:///repo/x.mjs:70")

    demo._prepare = prepare  # type: ignore[method-assign]
    doc = demo.open_path(str(source))
    session = demo.start(doc)
    assert until(lambda: session.state == "error")

    said = session.error
    assert said and str(tmp_path) not in said and "file://" not in said
    assert str(tmp_path) in capsys.readouterr().err

    space.paths["space-doc"] = str(source)
    session.state = "idle"
    with pytest.raises(state.Undrawn) as told:
        demo.pages_of_document("space-doc")
    assert str(tmp_path) not in str(told.value) and "file://" not in str(told.value)


def test_a_build_counts_the_files_its_documents_carry_and_says_its_total_once_known(
    demo: Demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files carried inside a document are found while the build starts; until they are, the
    build says no total rather than one that grows a moment later."""
    looked = threading.Event()

    def carried(path: Path) -> list[Any]:
        looked.wait(5)
        return [SimpleNamespace(name="inner.docx", content=b"inner")] \
            if path.name == "outer.pdf" else []

    monkeypatch.setattr(state, "embedded_in", carried)

    def run(session: Any) -> None:
        read_as("a" if session.source.name == "outer.pdf" else "b", session.source.name)(session)

    demo._run = run  # type: ignore[method-assign]
    (tmp_path / "outer.pdf").write_bytes(b"%PDF-1.4")
    build = demo.build_corpus([demo.open_path(str(tmp_path / "outer.pdf"))], "test")

    assert build.total == 0
    looked.set()
    assert until(lambda: build.state == "done"), build
    assert build.carried == 1 and build.documents == 2


def test_the_carried_count_does_not_shift_when_a_given_document_is_already_held(
    demo: Demo, space: FakeSpace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    monkeypatch.setattr(
        state, "embedded_in",
        lambda path: [SimpleNamespace(name="inner.docx", content=b"inner")]
        if path.name == "outer.pdf" else [])
    space.hashes.add(hashlib.sha256(b"%PDF-1.4 held").hexdigest())

    def run(session: Any) -> None:
        name = session.source.name
        read_as("a" if name == "outer.pdf" else "b", name)(session)

    demo._run = run  # type: ignore[method-assign]
    (tmp_path / "outer.pdf").write_bytes(b"%PDF-1.4 outer")
    (tmp_path / "held.pdf").write_bytes(b"%PDF-1.4 held")
    build = demo.build_corpus([demo.open_path(str(tmp_path / name))
                               for name in ("held.pdf", "outer.pdf")], "test")
    assert until(lambda: build.state == "done"), build
    assert build.carried == 1 and build.known == 1


def test_a_cancel_stops_a_reading_between_its_pages(demo: Demo, tmp_path: Path) -> None:
    """The outline is read a page at a time: a cancel heard as a page lands stops the pages
    after it, and the document says it was cancelled -- not failed, not read."""
    path = tmp_path / "memo.pdf"
    path.write_bytes(b"%PDF-1.4 memo")
    session = demo.session(demo.open_path(str(path)))
    session.pages = tuple(RenderedPage(index=i, width=10, height=10, png=b"") for i in (1, 2, 3))
    session.extracted = _doc("a", "memo.pdf")
    stop = session.stop = threading.Event()
    heard: list[int] = []

    class Builder:
        def read_figures(self, extracted: Any, source: Any) -> Any:
            return extracted

        def group_text(self, extracted: Any, source: Any) -> Any:
            return extracted

        def read_outline(self, extracted: Any, source: Any, on_page: Any) -> Any:
            for index in (1, 2, 3):
                heard.append(index)
                stop.set()  # the cancel arrives while the first page is read
                on_page(index, ())
            return extracted

    demo._builder = lambda s: Builder()  # type: ignore[method-assign,return-value]
    demo._steps(session, demo._read, lambda s: heard.append(-1))

    assert heard == [1]
    assert session.state == "cancelled"
    assert session.error == state.CANCELLED


# Refusals are said without HTTP: the route that called says what each means.


def test_what_the_state_refuses_it_refuses_without_http(demo: Demo, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="지원하지 않는 형식"):
        demo.add("notes.txt", b"x")
    with pytest.raises(ValueError, match="빈 파일"):
        demo.add("memo.docx", b"")
    with pytest.raises(LookupError):
        demo.session("nothing")
    with pytest.raises(LookupError):
        demo.pages_of_document("not-in-the-space")

    release = threading.Event()
    demo._run = lambda session: release.wait(5)  # type: ignore[method-assign]
    (tmp_path / "a.docx").write_bytes(b"a")
    doc = demo.open_path(str(tmp_path / "a.docx"))
    demo.build_corpus([doc], "")
    with pytest.raises(state.Busy):
        demo.build_corpus([doc], "")
    release.set()


def test_the_graph_space_is_the_one_connection_the_api_holds(demo: Demo, space: FakeSpace) -> None:
    """One Neo4j connection for the server: the state's space is its GraphMe's."""
    assert demo.space() is space is demo.graph.graph_space()



def test_two_first_requests_at_once_open_one_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The web app asks for the list and the map together on its first page: the graph space
    is opened once, not once each."""
    from graphme import api

    opened: list[FakeSpace] = []

    def slow(settings: Any) -> FakeSpace:
        time.sleep(0.1)
        opened.append(FakeSpace())
        return opened[-1]

    monkeypatch.setattr(api, "create_graph_space", slow)
    demo = Demo(SETTINGS, renderer=None, root=tmp_path / "server")  # type: ignore[arg-type]
    asks = [threading.Thread(target=demo.space) for _ in range(2)]
    for ask in asks:
        ask.start()
    for ask in asks:
        ask.join()

    assert len(opened) == 1


def test_two_first_questions_at_once_make_one_rag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first two questions can arrive together: the RAG engine is made once, not once each."""
    from graphme import api

    made: list[object] = []

    def slow(settings: Any, space: Any, client: Any = None) -> object:
        time.sleep(0.1)
        made.append(object())
        return made[-1]

    monkeypatch.setattr(api, "create_graph_space", lambda settings: FakeSpace())
    monkeypatch.setattr(api, "create_graph_rag", slow)
    demo = Demo(SETTINGS, renderer=None, root=tmp_path / "server")  # type: ignore[arg-type]
    asks = [threading.Thread(target=demo.rag) for _ in range(2)]
    for ask in asks:
        ask.start()
    for ask in asks:
        ask.join()

    assert len(made) == 1

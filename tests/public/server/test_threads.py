"""Conversations kept on this computer, and a turn's stages streamed as they finish. No model,
no Neo4j: the questions go to a fake GraphRAG. Invented documents only."""
from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from graphme.adapters.storage import graph_rag, local_neo4j
from graphme.adapters.storage.graph_rag import Claim, GraphAnswer, GraphSource, Turn
from graphme.server import app as server_app
from graphme.server import models as api
from graphme.server import state
from graphme.server.state import Demo
from graphme.server.threads import ThreadStore
from tests.public.server.conftest import SETTINGS

SOURCE = GraphSource(id="d:0", score=0.9, document_id="d", file="memo.docx",
                     members=["n1"], context="memo.docx\nThe hold time is 4 h.")


class StagedRag:
    """Finds one passage and answers from it, saying the answer before its claims are checked
    (`shown`), as the real GraphRAG's `written` does."""
    text_only = False

    def __init__(self) -> None:
        self.histories: list[list[Turn]] = []
        self.only: list[frozenset[str] | None] = []
        self.exclude: list[frozenset[str]] = []
        self.stages: list[str] = []
        self.looked: list[bool] = []  # each scope lookup, and whether inside a turn

    def find(self, question: str, *, documents: int, history: Any = (),
             only: Any = None, exclude: Any = frozenset()) -> list[GraphSource]:
        self.stages.append("find" if graph_rag._TURN.get() is not None else "find unheld")
        return [SOURCE]

    def ask(self, question: str, *, documents: int, history: Any = (), only: Any = None,
            exclude: Any = frozenset(),
            shown: Callable[[GraphAnswer], None] | None = None) -> GraphAnswer:
        self.stages.append("answer" if graph_rag._TURN.get() is not None else "answer unheld")
        self.histories.append(list(history))
        self.only.append(only)
        self.exclude.append(exclude)
        answer = GraphAnswer(text="4 h [memo.docx]", sources=[SOURCE])
        if shown is not None:
            shown(answer)
        self.stages.append("check")
        return GraphAnswer(text=answer.text, sources=answer.sources, checked=True,
                           claims=[Claim("4 h", True, None, ["memo.docx"])])

    def last_queries(self, question: str, history: Any = ()) -> tuple[str, ...]:
        return (question,)

    rebuilt: dict[str, str] = {}
    """Documents edited and built again: the id chosen to the id the graph holds now."""

    def current(self, ids: frozenset[str]) -> dict[str, str]:
        self.looked.append(graph_rag._TURN.get() is not None)
        return {i: self.rebuilt.get(i, i) for i in ids}


def _events(body: str) -> list[tuple[str, Any]]:
    said = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        said.append((lines["event"], json.loads(lines["data"])))
    return said


@pytest.fixture
def rag(demo: Demo) -> StagedRag:
    made = StagedRag()
    demo.graph._rag = made  # type: ignore[assignment]
    return made


def test_a_thread_is_made_listed_read_renamed_and_deleted(tmp_path: Path) -> None:
    store = ThreadStore(tmp_path / "threads")
    made = store.create(["d"], "default")
    assert store.get(made.id).scope == ["d"]
    assert [t.id for t in store.list()] == [made.id]
    store.append(made.id, api.ThreadTurn(question="Hold time?", asked_at=1.0))
    assert store.get(made.id).title == "Hold time?"  # the first question names it
    assert store.patch(made.id, title="Ovens").title == "Ovens"
    assert store.patch(made.id, scope=None, scope_given=True).scope is None
    assert store.list()[0].turns == 1
    store.delete(made.id)
    assert store.list() == []
    with pytest.raises(KeyError):
        store.get(made.id)
    with pytest.raises(KeyError):
        store.get("../workspaces")  # an id names a file here, never a path


def test_a_corrupt_thread_is_skipped(tmp_path: Path) -> None:
    store = ThreadStore(tmp_path)
    kept = store.create(None, "default")
    (tmp_path / "0123456789abcdef.json").write_text("{not json")
    (tmp_path / "fedcba9876543210.json").write_text(json.dumps({"id": "fedcba9876543210"}))
    # a whole thread under another name is not that thread
    (tmp_path / "00000000000000aa.json").write_text(
        (tmp_path / f"{kept.id}.json").read_text())
    assert [t.id for t in store.list()] == [kept.id]


def test_a_turn_streams_its_stages_in_order(demo: Demo, client: TestClient,
                                             rag: StagedRag) -> None:
    thread = client.post("/v1/threads", json={"scope": ["d"]}).json()
    turned = client.post(f"/v1/threads/{thread['id']}/turns",
                         json={"question": "Hold time?", "stamp": "s1"},
                         headers={"Accept": "text/event-stream"})
    assert turned.status_code == 200
    assert turned.headers["content-type"].startswith("text/event-stream")
    events = _events(turned.text)
    assert [name for name, _ in events] == ["searched", "answered", "checked", "saved"]
    assert events[0][1]["sources"][0]["file"] == "memo.docx"
    assert events[1][1]["answer"] == "4 h [memo.docx]" and events[1][1]["claims"] == []
    assert events[2][1] == {"claims": [{"text": "4 h", "supported": True, "correction": None,
                                        "files": ["memo.docx"]}], "checked": True}
    assert rag.only == [frozenset({"d"})]  # the thread's scope
    # both stages inside one turn's search (`one_search`), the check after the answer
    assert sorted(rag.stages) == ["answer", "check", "find"]
    kept = client.get(f"/v1/threads/{thread['id']}").json()
    assert kept["title"] == "Hold time?"
    assert kept["turns"] == [events[3][1]]
    assert kept["turns"][0]["answer"]["checked"] is True and kept["turns"][0]["stamp"] == "s1"
    assert [t["id"] for t in client.get("/v1/threads").json()] == [thread["id"]]


def test_a_turn_sends_the_earlier_turns_as_history(demo: Demo, client: TestClient,
                                                    rag: StagedRag) -> None:
    thread = client.post("/v1/threads", json={}).json()
    first = client.post(f"/v1/threads/{thread['id']}/turns", json={"question": "Hold time?"},
                        headers={"Accept": "application/json"})
    assert first.status_code == 200 and first.json()["answer"]["answer"] == "4 h [memo.docx]"
    client.post(f"/v1/threads/{thread['id']}/turns", json={"question": "And for QP-2?"},
                headers={"Accept": "application/json"})
    assert rag.histories == [[], [Turn("Hold time?", "4 h [memo.docx]")]]
    assert rag.only == [None, None]  # no scope: every document


def test_a_turn_without_a_document_leaves_it_out_of_that_turn_alone(
        demo: Demo, client: TestClient, rag: StagedRag) -> None:
    thread = client.post("/v1/threads", json={"scope": ["d", "e"]}).json()
    turns = f"/v1/threads/{thread['id']}/turns"
    json_only = {"Accept": "application/json"}
    client.post(turns, json={"question": "Hold time?", "exclude": ["e"]}, headers=json_only)
    client.post(turns, json={"question": "And after?"}, headers=json_only)
    assert rag.exclude == [frozenset({"e"}), frozenset()]
    assert rag.only == [frozenset({"d", "e"})] * 2  # the scope is the thread's, untouched
    kept = client.get(f"/v1/threads/{thread['id']}").json()
    assert kept["scope"] == ["d", "e"]
    # each turn says what it left out, so asking it again leaves it out again
    assert [turn["exclude"] for turn in kept["turns"]] == [["e"], None]


def test_a_thread_scoped_to_a_document_still_finds_it_after_the_file_is_rebuilt(
        demo: Demo, client: TestClient, rag: StagedRag) -> None:
    thread = client.post("/v1/threads", json={"scope": ["d", "e"]}).json()
    rag.rebuilt = {"d": "d2"}  # memo.docx (d) edited and built again as d2
    turns = f"/v1/threads/{thread['id']}/turns"
    client.post(turns, json={"question": "Hold time?"}, headers={"Accept": "application/json"})

    kept = client.get(f"/v1/threads/{thread['id']}").json()
    assert kept["scope"] == ["d2", "e"]
    assert kept["turns"][0]["scope"] == ["d2", "e"]
    assert rag.looked == [True]  # looked up once, inside the turn


def test_a_failed_turn_is_said_and_kept_with_why(demo: Demo, client: TestClient,
                                                 rag: StagedRag) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("/Users/someone/secret path")
    rag.find = broken  # type: ignore[method-assign]
    thread = client.post("/v1/threads", json={}).json()
    events = _events(client.post(f"/v1/threads/{thread['id']}/turns",
                                 json={"question": "Hold time?"}).text)
    assert [name for name, _ in events] == ["saved", "error"]
    assert "RuntimeError" in events[1][1]["message"]
    assert "/Users" not in events[1][1]["message"]
    assert client.get(f"/v1/threads/{thread['id']}").json()["turns"][0]["error"]


def test_a_turn_turned_away_before_streaming_is_a_plain_400(client: TestClient,
                                                           rag: StagedRag) -> None:
    thread = client.post("/v1/threads", json={}).json()
    told = client.post(f"/v1/threads/{thread['id']}/turns", json={"question": " "})
    assert told.status_code == 400
    assert client.post("/v1/threads/0123456789abcdef/turns",
                       json={"question": "x"}).status_code == 404
    assert client.get("/v1/threads/..%2Fsecret").status_code == 404


def test_a_thread_is_renamed_rescoped_and_deleted(client: TestClient) -> None:
    thread = client.post("/v1/threads", json={"scope": ["d"]}).json()
    renamed = client.patch(f"/v1/threads/{thread['id']}", json={"title": "Ovens"}).json()
    assert renamed["title"] == "Ovens" and renamed["scope"] == ["d"]  # scope left out: kept
    assert client.patch(f"/v1/threads/{thread['id']}", json={"scope": None}).json()["scope"] is None
    assert client.delete(f"/v1/threads/{thread['id']}").status_code == 200
    assert client.get(f"/v1/threads/{thread['id']}").status_code == 404


def test_ask_without_event_stream_is_unchanged(client: TestClient, rag: StagedRag) -> None:
    answered = client.post("/v1/ask", json={"question": "Hold time?"})
    assert answered.headers["content-type"] == "application/json"
    assert answered.json()["answer"] == "4 h [memo.docx]"
    assert answered.json()["checked"] is True


def test_ask_streams_its_stages_with_no_save(client: TestClient, rag: StagedRag) -> None:
    streamed = client.post("/v1/ask", json={"question": "Hold time?", "only": ["d"]},
                           headers={"Accept": "text/event-stream"})
    assert [name for name, _ in _events(streamed.text)] == ["searched", "answered", "checked"]


def test_threads_live_in_the_workspace_folder(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.public.server.test_workspaces import _Graph
    monkeypatch.setattr(state, "GraphMe", _Graph)
    monkeypatch.setattr(local_neo4j, "stop_graph", lambda graph: None)
    home = tmp_path / "home"
    demo = Demo(SETTINGS.model_copy(update={"local_neo4j": home}), renderer=None,  # type: ignore[arg-type]
                root=home / "server")
    other = demo.workspace_registry().create("Ovens")["id"]
    assert demo.threads().folder == home / ".graphme" / "threads"  # the home's own graph
    made = demo.threads(other).create(None, other)
    assert demo.threads(other).folder == home / "workspaces" / other / "threads"
    assert demo.thread_store(made.id).folder == demo.threads(other).folder
    with pytest.raises(LookupError):
        demo.threads("nowhere")


def _stopped_at(stage: str) -> Callable[[Callable[[Any], object]], Response]:
    """`streamed` with its client gone just as `stage` is said."""
    def streamed(work: Callable[[Any], object]) -> Response:
        def said(name: str, _: Any) -> None:
            if name == stage:
                raise server_app.Stopped
        with contextlib.suppress(server_app.Stopped):
            work(said)
        return Response("gone")
    return streamed


@pytest.mark.parametrize("stage", ["checked", "answered"])
def test_a_turn_whose_client_left_keeps_what_was_paid_for(
        stage: str, client: TestClient, rag: StagedRag, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_app, "streamed", _stopped_at(stage))
    thread = client.post("/v1/threads", json={}).json()
    client.post(f"/v1/threads/{thread['id']}/turns", json={"question": "Hold time?"})

    [kept] = client.get(f"/v1/threads/{thread['id']}").json()["turns"]
    assert kept["found"]["sources"][0]["file"] == "memo.docx"
    assert kept["answer"]["answer"] == "4 h [memo.docx]"
    assert kept["answer"]["queries"] == ["Hold time?"] and kept["error"] is None
    if stage == "answered":  # gone before the check: it is not run, and the answer unchecked
        assert "check" not in rag.stages and kept["answer"]["checked"] is False
    else:  # the check ran before the client left: kept with the answer
        assert kept["answer"]["checked"] is True


def test_a_thread_deleted_mid_turn_says_so(demo: Demo, client: TestClient,
                                          rag: StagedRag) -> None:
    thread = client.post("/v1/threads", json={}).json()
    asked = rag.ask

    def deleting(*args: Any, **kwargs: Any) -> GraphAnswer:
        client.delete(f"/v1/threads/{thread['id']}")
        return asked(*args, **kwargs)
    rag.ask = deleting  # type: ignore[method-assign]
    events = _events(client.post(f"/v1/threads/{thread['id']}/turns",
                                 json={"question": "Hold time?"}).text)
    assert [name for name, _ in events] == ["searched", "answered", "checked", "error"]
    assert "deleted" in events[-1][1]["message"]

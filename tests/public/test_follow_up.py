"""A follow-up question leans on the turns before it: it is searched as the question it stands
for, and answered with those turns above its passages. Invented questions and passages only;
no model or Neo4j is reached."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import neo4j
import pytest

from graphme.adapters.chat import Endpoint
from graphme.adapters.storage.graph_rag import SPLITS, GraphRag, Turn

EARLIER = [Turn("What does WO-1 follow?", "It follows QP-9 [WO-1.docx].")]
_PASSAGE = {
    "id": "qp:0", "score": 0.9, "document_id": "qp", "file": "QP-9.docx",
    "text": "The invented hold time is ten minutes.", "members": ["qp-p"], "quote_of": None,
    "modified": None, "opening": None, "pages": [], "carriers": [], "linked": [],
}


@pytest.fixture(autouse=True)
def _no_split_kept() -> Iterator[None]:
    SPLITS._kept.clear()
    yield
    SPLITS._kept.clear()


class _Retriever:
    """The fused search, keeping each query it is asked."""

    text_only, embedder, neo4j_database = True, None, None

    def __init__(self) -> None:
        self.searched: list[str] = []

    def fused(self, query: str, only: object = None) -> list[neo4j.Record]:
        self.searched.append(query)
        return [neo4j.Record(_PASSAGE)]  # type: ignore[no-untyped-call]


def _rag(split: dict[str, Any], sent: list[httpx.Request]) -> tuple[GraphRag, _Retriever]:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        said = json.dumps(split) if b"question_split" in request.content else "Ten minutes."
        return httpx.Response(200, json={"choices": [{"message": {"content": said}}]})

    rag = GraphRag(
        SimpleNamespace(driver=None, identities=lambda: [], version_pairs=list),  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
        endpoint=Endpoint("chat_completions", "https://follow.test/v1", "invented-chat", None),
        client=httpx.Client(transport=httpx.MockTransport(handle)), model="m", dimensions=3,
        relevance_floor=0.0, dense_weight=0.6, rrf_k=60, wait=lambda _: None,
    )
    rag._samples = []
    retriever = _Retriever()
    rag._rag = retriever  # type: ignore[assignment]
    return rag, retriever


def _split_body(sent: list[httpx.Request]) -> bytes:
    [split] = [request for request in sent if b"question_split" in request.content]
    return split.content


def test_a_follow_up_is_searched_as_the_standalone_question() -> None:
    sent: list[httpx.Request] = []
    rag, retriever = _rag({"queries": ["QP-9 hold time"], "standalone": "QP-9 hold time"}, sent)

    rag.find("How long is its hold time?", documents=1, history=EARLIER)

    assert retriever.searched == ["QP-9 hold time"]
    assert rag.last_queries("How long is its hold time?", EARLIER) == ("QP-9 hold time",)


def test_only_the_last_turns_are_sent() -> None:
    sent: list[httpx.Request] = []
    rag, _ = _rag({"queries": ["invented"], "standalone": ""}, sent)
    history = [Turn(f"Invented question number {n}?", f"Invented answer {n}.") for n in range(5)]

    rag.find("And the next one?", documents=1, history=history)

    body = _split_body(sent).decode()
    assert "Invented question number 0?" not in body
    assert "Invented question number 1?" not in body
    assert all(f"Invented question number {n}?" in body for n in (2, 3, 4))


def test_no_history_asks_as_today() -> None:
    sent: list[httpx.Request] = []
    rag, retriever = _rag({"queries": ["invented hold time"]}, sent)

    rag.find("What is the invented hold time?", documents=1)

    # The split request of a question with no earlier turns, byte for byte.
    assert hashlib.sha256(_split_body(sent)).hexdigest() == TODAY
    assert retriever.searched == ["What is the invented hold time?"]


TODAY = "dea9cf08615163da6ace4a9cac0596bfbe6d6cfa1851d23b484b3617b74ee020"


def test_the_answer_reads_the_earlier_turns() -> None:
    sent: list[httpx.Request] = []
    rag, _ = _rag({"queries": ["QP-9 hold time"], "standalone": "QP-9 hold time"}, sent)

    answer = rag.ask("How long is its hold time?", documents=1, history=EARLIER)

    [answering] = [r for r in sent if b"question_split" not in r.content]
    prompt = json.loads(answering.content)["messages"][1]["content"][0]["text"]
    assert prompt.index("What does WO-1 follow?") < prompt.index("Context:")
    assert prompt.rstrip().endswith("Answer in English:")
    assert "How long is its hold time?" in prompt  # answered in the user's own words
    assert answer.text and rag.last_queries(
        "How long is its hold time?", EARLIER) == ("QP-9 hold time",)


def test_an_empty_history_or_a_nothing_found_turn_asks_the_question_as_written() -> None:
    from graphme.adapters.storage.answer_prompt import _nothing_found

    question = "What is the invented hold time?"
    sent: list[httpx.Request] = []
    rag, retriever = _rag({"queries": ["invented hold time"]}, sent)
    rag.find(question, documents=1, history=[])
    assert hashlib.sha256(_split_body(sent)).hexdigest() == TODAY  # as with no history at all
    assert retriever.searched == [question]

    SPLITS._kept.clear()
    sent.clear()
    rag, retriever = _rag({"queries": ["invented hold time"], "standalone": ""}, sent)
    nothing = [Turn("What is the invented rest time?",
                    _nothing_found("What is the invented rest time?"))]
    answer = rag.ask(question, documents=1, history=nothing)
    assert retriever.searched == [question] and answer.text == "Ten minutes."

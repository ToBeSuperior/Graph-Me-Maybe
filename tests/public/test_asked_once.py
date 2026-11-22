"""What one question costs over several workspaces: its words embedded once, its split asked
once, and the pictures an answer is sent taken by how the passages they came with ranked.
Invented questions and passages only; no model or Neo4j is reached."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import neo4j
from neo4j_graphrag.types import RetrieverResultItem

from graphme.adapters.chat import Endpoint
from graphme.adapters.embedding import OpenAIBatchEmbeddings
from graphme.adapters.storage.graph_rag import (
    ANSWER_PICTURES,
    GraphRag,
    ask_across,
    one_search,
)


def _embedder(calls: list[httpx.Request]) -> OpenAIBatchEmbeddings:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"object": "list", "model": "m", "data": [
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1}})

    return OpenAIBatchEmbeddings(
        model="invented-embedder", api_key="local", base_url="http://127.0.0.1:2/v1",
        query_prefix="Query: ",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)))


def test_one_question_is_embedded_once_however_many_readers_ask() -> None:
    calls: list[httpx.Request] = []
    first, second = _embedder(calls), _embedder(calls)

    assert first.embed_query("invented lantern spec") == [1.0, 0.0]
    assert second.embed_query("invented lantern spec") == [1.0, 0.0]
    assert first.embed_query("invented lantern spec") == [1.0, 0.0]

    assert len(calls) == 1
    first.embed_query("another invented question")
    assert len(calls) == 2


def _rag(calls: list[httpx.Request]) -> GraphRag:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        content = json.dumps({"queries": ["a lantern", "a kettle"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return GraphRag(
        SimpleNamespace(driver=None), embedder=None,  # type: ignore[arg-type]
        endpoint=Endpoint("chat_completions", "https://split.test/v1", "invented-chat", None),
        client=httpx.Client(transport=httpx.MockTransport(handle)), model="m", dimensions=3,
        relevance_floor=0.0, dense_weight=0.6, rrf_k=60, wait=lambda _: None,
    )


def test_two_workspaces_asked_the_same_question_split_it_once() -> None:
    calls: list[httpx.Request] = []
    one, other = _rag(calls), _rag(calls)

    assert one._asked("a lantern and a kettle, invented")[0] == ("a lantern", "a kettle")
    assert other._asked("a lantern and a kettle, invented")[0] == ("a lantern", "a kettle")

    assert len(calls) == 1


class _Reader:
    """A workspace's reader whose passages each hold the pictures named in `held`."""

    def __init__(self, held: dict[str, int]) -> None:
        self.held = held
        self.sent: list[tuple[str, bytes]] = []

    def pictures(self, items: list[RetrieverResultItem]) -> list[list[tuple[str, bytes]]]:
        return [[(f"{i.metadata['id']} picture {n}", b"png")  # type: ignore[index]
                 for n in range(self.held[i.metadata["id"]])] for i in items]  # type: ignore[index]

    _check_answers = False
    written = GraphRag.written
    _link_discount = 0.85  # what `written` ranks linked documents with (`promoted`)

    def answered(self, question: str, items: Any, pictures: list[tuple[str, bytes]],
                 history: Any = ()) -> str:
        self.sent = pictures
        return "invented answer"


def _item(passage: str, document: str) -> RetrieverResultItem:
    return RetrieverResultItem(content=f"{passage} words", metadata={
        "id": passage, "score": 0.5, "document_id": document, "file": f"{document}.docx",
        "members": [], "linked": [], "modified": None, "origin": None})


def test_an_answers_pictures_follow_how_their_passages_ranked_not_the_workspaces() -> None:
    first = _Reader({"a:0": 0, "a:1": ANSWER_PICTURES})
    second = _Reader({"b:0": 1})

    ask_across([("first", first, [_item("a:0", "memo"), _item("a:1", "memo")]),  # type: ignore[list-item]
                 ("second", second, [_item("b:0", "spec")])],  # type: ignore[list-item]
                "invented", 2, rrf_k=60)

    # Fused: a:0, b:0, a:1 -- the second workspace's picture goes ahead of a:1's.
    assert first.sent[0] == ("b:0 picture 0", b"png")
    assert len(first.sent) == ANSWER_PICTURES


class _Retriever:
    """Searches by words alone (no links to walk), counting each fused search."""
    text_only = True

    def __init__(self) -> None:
        self.fused_for: list[str] = []

    def fused(self, query: str, only: Any) -> list[Any]:
        self.fused_for.append(query)
        return [neo4j.Record({"id": f"memo:{len(self.fused_for)}", "document_id": "memo"})]  # type: ignore[no-untyped-call]


def test_a_turn_searches_once_even_when_its_answer_starts_after_the_search() -> None:
    rag = _rag([])
    rag._samples = []
    retriever = _Retriever()
    rag._retriever = lambda: retriever  # type: ignore[method-assign,assignment,return-value]

    with one_search():
        shown = rag._found("a lantern and a kettle, invented", 1)
        read = rag._found("a lantern and a kettle, invented", 1, read_on=True)

    assert retriever.fused_for == ["a lantern", "a kettle"]  # once a query, for both stages
    assert {r["id"] for r in shown} <= {r["id"] for r in read}  # the answer reads on past them
    rag._found("a lantern and a kettle, invented", 1)  # another question's turn searches again
    assert len(retriever.fused_for) == 4

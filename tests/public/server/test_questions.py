"""Questions over the graph space: what they find, what they are answered with, and why one
is not."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from graphme.adapters.storage.graph_rag import (
    MAX_TOP_K,
    GraphAnswer,
    GraphNotEmbedded,
    GraphSource,
    LinkedDocument,
)
from graphme.server import app as server_app
from graphme.server.state import Demo


class FakeRag:
    def __init__(self, failure: Exception | None = None) -> None:
        self.asked: list[int] = []
        self.failure = failure

    def ask(self, question: str, *, top_k: int) -> GraphAnswer:
        if self.failure:
            raise self.failure
        self.asked.append(top_k)
        return GraphAnswer(
            text="합격 [spec.docx]",
            sources=[GraphSource(id="d:0", score=0.9, document_id="d", file="spec.docx",
                                 members=["n1"], context="spec.docx\n합격")],
        )

    def find(self, question: str, *, top_k: int) -> list[GraphSource]:
        if self.failure:
            raise self.failure
        return [GraphSource(id="d:0", score=0.9, document_id="d", file="memo.docx",
                            members=["n1"], context="memo", linked=[LinkedDocument(
                                document_id="s", file="spec.docx", hop=2, via="d",
                                direction="cites", passages=[])])][:top_k]


def _asking(demo: Demo, rag: Any) -> None:
    demo.graph._rag = rag


def test_a_question_is_answered_with_its_sources_and_a_bad_one_is_told_why(
    demo: Demo, client: TestClient
) -> None:
    rag = FakeRag()
    _asking(demo, rag)

    answered = client.post("/v1/ask", json={"question": "판정은?"})
    assert answered.status_code == 200
    assert answered.json()["sources"][0]["members"] == ["n1"]
    assert rag.asked == [demo.settings.retrieval_top_k]

    for top_k in ("many", 0, MAX_TOP_K + 1):
        told = client.post("/v1/ask", json={"question": "판정은?", "top_k": top_k})
        assert told.status_code == 400, top_k
    assert client.post("/v1/ask", json={"question": " "}).status_code == 400


def test_a_search_finds_documents_with_no_answer_written(demo: Demo, client: TestClient) -> None:
    _asking(demo, FakeRag())

    found = client.post("/v1/search", json={"question": "누설", "top_k": 10})

    assert found.status_code == 200
    [source] = found.json()["sources"]
    assert source["file"] == "memo.docx" and source["linked"][0]["file"] == "spec.docx"
    assert "answer" not in found.json()
    assert client.post("/v1/search", json={"question": ""}).status_code == 400


def test_a_question_too_long_to_be_one_is_turned_away_before_any_model(
    demo: Demo, client: TestClient
) -> None:
    _asking(demo, FakeRag(AssertionError("never searched")))
    limit = server_app.MAX_QUESTION_CHARS

    for route in ("/v1/search", "/v1/ask"):
        told = client.post(route, json={"question": "가" * (limit + 1)})
        assert told.status_code == 400 and f"{limit:,}" in told.json()["detail"], route


def test_a_question_without_an_embedding_key_says_what_is_missing(client: TestClient) -> None:
    """The test settings' embedder has no key to embed with: GraphMe says which is needed."""
    told = client.post("/v1/ask", json={"question": "x"})

    assert told.status_code == 503 and "GRAPHME_EMBEDDING_PROVIDER" in told.json()["detail"]


def test_a_space_not_embedded_yet_is_a_409(demo: Demo, client: TestClient) -> None:
    _asking(demo, FakeRag(GraphNotEmbedded("embed the graph space first")))

    told = client.post("/v1/search", json={"question": "x"})

    assert told.status_code == 409 and told.json()["detail"] == "embed the graph space first"


def test_a_model_that_fails_is_a_502_said_without_this_machines_paths(
    demo: Demo, client: TestClient
) -> None:
    _asking(demo, FakeRag(RuntimeError("/private/path/of/this/machine")))

    told = client.post("/v1/ask", json={"question": "x"})

    assert told.status_code == 502 and "/private/path" not in told.json()["detail"]


def test_a_question_with_the_space_down_is_a_503(demo: Demo) -> None:
    def down() -> None:
        raise ConnectionError("no Neo4j")

    demo.graph._graph_space = down  # type: ignore[method-assign]
    client = TestClient(server_app.create_app(demo, web=None), base_url="http://127.0.0.1:8765",
                        raise_server_exceptions=False)

    for route in ("/v1/ask", "/v1/search"):
        assert client.post(route, json={"question": "x"}).status_code == 503, route


def test_a_graph_db_lost_mid_question_is_a_503_not_a_model_failure(demo: Demo) -> None:
    """Reached once and lost since: still the graph DB that cannot be reached (503)."""
    from neo4j.exceptions import ServiceUnavailable

    _asking(demo, FakeRag(ServiceUnavailable("invented: connection lost")))
    client = TestClient(server_app.create_app(demo, web=None), base_url="http://127.0.0.1:8765",
                        raise_server_exceptions=False)

    told = client.post("/v1/ask", json={"question": "x"})
    assert told.status_code == 503 and "invented" not in told.json()["detail"]

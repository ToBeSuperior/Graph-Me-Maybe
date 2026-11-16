"""One question over several workspaces: each searched in its own scope, side by side, the
ranked lists fused by reciprocal rank with each workspace weighted the same, and each result
naming the workspace it came from. Invented workspaces and passages only."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neo4j_graphrag.types import RetrieverResultItem

from graphme.adapters.storage import local_neo4j
from graphme.adapters.storage.graph_rag import GraphNotEmbedded, GraphSource
from graphme.server import create_app, state
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST
from tests.public.server.conftest import SETTINGS, FakeSpace

BASE = "http://127.0.0.1:8765"


class _Rag:
    """A workspace's search: the same document ids in every workspace (ids are content-derived),
    each passage's words naming the workspace it is in. One never embedded has no vector
    index, and is refused whether or not it is asked by words alone, as `GraphRag` is."""

    def __init__(self, workspace: str, *, embedded: bool = True) -> None:
        self.workspace, self.embedded = workspace, embedded
        self.text_only = False
        self.answered_from: list[str] = []

    def _check(self) -> None:
        if not self.embedded:
            raise GraphNotEmbedded("no vector index")

    def find(self, question: str, *, documents: int) -> list[GraphSource]:
        self._check()
        return [GraphSource(id=f"{doc}:{n}", score=0.5, document_id=doc, file=f"{doc}.docx",
                            members=[f"{doc}-u{n}"], context=f"{self.workspace} {doc} {n}")
                for doc in ("memo", "spec") for n in (0, 1)][: 2 * documents]

    def read(self, question: str, *, documents: int) -> list[RetrieverResultItem]:
        return [RetrieverResultItem(content=s.context, metadata={
                    "id": s.id, "score": s.score, "document_id": s.document_id, "file": s.file,
                    "members": s.members, "linked": [], "modified": None, "origin": None})
                for s in self.find(question, documents=documents)]

    def pictures(self, items: list[RetrieverResultItem]) -> list[list[tuple[str, bytes]]]:
        assert all(str(i.content).startswith(self.workspace) for i in items)  # never crosses
        return [[] for _ in items]

    def answered(self, question: str, items: list[RetrieverResultItem],
                 pictures: list[tuple[str, bytes]]) -> str:
        self.answered_from = [str(i.content) for i in items]
        return "invented answer"


class _Graph:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.opened_space = self._space = FakeSpace()
        self._rag = _Rag(settings.workspace)

    def held_to(self, settings: Any, *, take: bool = False) -> _Graph:
        return type(self)(settings)

    def graph_space(self) -> FakeSpace:
        return self._space

    def rag(self) -> _Rag:
        return self._rag

    def forget_rag(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def demo_(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Demo:
    monkeypatch.setattr(state, "GraphMe", _Graph)
    monkeypatch.setattr(local_neo4j, "stop_graph", lambda graph: None)
    home = tmp_path / "home"
    return Demo(SETTINGS.model_copy(update={"local_neo4j": home, "workspace": FIRST}),
                renderer=None, root=home / "server")  # type: ignore[arg-type]


def _made(demo: Demo, name: str) -> str:
    return str(demo.workspaces.create(name)["id"])  # type: ignore[union-attr]


def test_a_search_over_two_workspaces_fuses_both_and_none_crosses(demo_: Demo) -> None:
    other = _made(demo_, "Invented project")
    client = TestClient(create_app(demo_), base_url=BASE)

    found = client.post("/v1/search", json={"question": "invented", "documents": 3,
                                            "workspaces": [FIRST, other]})

    assert found.status_code == 200, found.text
    sources = found.json()["sources"]
    # Ranks taken in turn, each workspace weighted the same; the same document id in two
    # workspaces is two documents, and the walk stops at the third.
    assert [(s["workspace"], s["id"]) for s in sources] == [
        (FIRST, "memo:0"), (other, "memo:0"), (FIRST, "memo:1"), (other, "memo:1"),
        (FIRST, "spec:0")]
    assert all(s["context"].startswith(s["workspace"]) for s in sources)


def test_one_workspace_is_searched_as_today_and_names_itself(demo_: Demo) -> None:
    client = TestClient(create_app(demo_), base_url=BASE)

    alone = client.post("/v1/search", json={"question": "invented", "documents": 1}).json()
    listed = client.post("/v1/search", json={"question": "invented", "documents": 1,
                                             "workspaces": []}).json()

    assert alone == listed
    assert [(s["workspace"], s["id"]) for s in alone["sources"]] == [
        (FIRST, "memo:0"), (FIRST, "memo:1")]


def test_a_workspace_never_embedded_adds_nothing_and_fails_nothing(demo_: Demo) -> None:
    other = _made(demo_, "Invented project")
    plain = _made(demo_, "Invented, never embedded")
    demo_.reader(plain)._rag.embedded = False  # type: ignore[attr-defined]
    client = TestClient(create_app(demo_), base_url=BASE)
    asked = {"question": "invented", "documents": 2, "workspaces": [plain, other]}

    found = client.post("/v1/search", json=asked)
    answered = client.post("/v1/ask", json=asked)

    assert found.status_code == 200, found.text
    assert {s["workspace"] for s in found.json()["sources"]} == {other}
    assert answered.status_code == 200, answered.text
    assert {s["workspace"] for s in answered.json()["sources"]} == {other}


def test_an_answer_reads_the_fused_passages_and_an_unknown_workspace_is_404(
    demo_: Demo
) -> None:
    other = _made(demo_, "Invented project")
    client = TestClient(create_app(demo_), base_url=BASE)

    answered = client.post("/v1/ask", json={"question": "invented", "documents": 2,
                                            "workspaces": [FIRST, other]})
    nowhere = client.post("/v1/search", json={"question": "invented",
                                              "workspaces": [FIRST, "no-such-workspace"]})

    assert answered.status_code == 200, answered.text
    assert answered.json()["answer"] == "invented answer"
    assert [(s["workspace"], s["id"]) for s in answered.json()["sources"]] == [
        (FIRST, "memo:0"), (other, "memo:0"), (FIRST, "memo:1"), (other, "memo:1")]
    assert nowhere.status_code == 404

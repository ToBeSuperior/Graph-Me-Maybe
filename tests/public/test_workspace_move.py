"""Graphs from before every workspace was in the home's one Neo4j, moved once: the home's own
relabelled as the default workspace, a workspace folder's own graph copied in under its scope.
No Neo4j: fake drivers answer from invented rows."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import neo4j
import pytest

from graphme.adapters.storage import workspace_move
from graphme.adapters.storage.workspace_move import (
    OLD_SCHEMA,
    MoveMismatch,
    copy_graph,
    move_home_graph,
)
from graphme.adapters.storage.workspace_scope import WorkspaceScope, unscoped_patterns

Answer = Callable[[str, dict[str, Any]], list[dict[str, Any]]]


class _Driver(neo4j.Driver):
    """Keeps every statement; `answer` gives each its rows."""

    def __init__(self, answer: Answer) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.answer = answer

    def _rows(self, query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.sent.append((query, parameters))
        return self.answer(query, parameters)

    def execute_query(self, query: str, parameters: dict[str, Any] | None = None,
                      **kw: Any) -> neo4j.EagerResult:
        given = {**(parameters or {}), **{k: v for k, v in kw.items() if not k.endswith("_")}}
        return neo4j.EagerResult(self._rows(query, given), None, [])  # type: ignore[arg-type]

    def session(self, **_: Any) -> _Session:
        return _Session(self)

    def queries(self) -> list[str]:
        return [query for query, _ in self.sent]


class _Session:
    def __init__(self, driver: _Driver) -> None:
        self.driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, parameters: dict[str, Any] | None = None, **kw: Any) -> Any:
        return iter(self.driver._rows(query, {**(parameters or {}), **kw}))

    def execute_write(self, work: Any, *args: Any) -> Any:
        return work(self, *args)


def _old_home(relabelled: list[int]) -> Answer:
    """A home graph from before workspaces: the old constraint there, two batches to relabel
    for the units, one for the passages, and embedded passages of size 3."""
    def answer(query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        if "SHOW CONSTRAINTS" in query:
            return [{"name": "doc_node_id"}]
        if "SET n:" in query:
            return [{"done": relabelled.pop(0)}]
        if "size(" in query:
            return [{"size": 3}]
        return []
    return answer


def test_the_home_s_graph_becomes_the_default_workspace_and_loses_the_old_schema() -> None:
    driver = _Driver(_old_home([2, 1, 0, 4, 0]))

    assert move_home_graph(driver, batch=2) is True

    sent = driver.queries()
    relabels = [q for q in sent if "SET n:" in q]
    assert len(relabels) == 5
    assert all("n:W_default" in q for q in relabels[:3])
    assert all("n:C_default" in q for q in relabels[3:])
    assert all("WITH n LIMIT $batch" in q for q in relabels)
    # Only nodes no workspace holds yet.
    assert "STARTS WITH 'W_'" in relabels[0] and "STARTS WITH 'C_'" in relabels[3]
    # The default's own schema and vector index, then the old global names dropped -- last,
    # since the old constraint is what says the move is not done.
    assert any("CREATE CONSTRAINT doc_node_id_default" in q for q in sent)
    assert any("CREATE VECTOR INDEX" in q and "C_default" in q
               and p["name"] == "chunk_embedding_default"
               for q, p in driver.sent)
    dropped = [q for q in sent if q.startswith("DROP ")]
    assert dropped == [f"DROP {kind} {name} IF EXISTS" for kind, name in OLD_SCHEMA]
    assert sent.index(dropped[0]) > max(sent.index(q) for q in relabels)
    assert {name for _, name in OLD_SCHEMA} == {
        "doc_node_id", "doc_node_document", "chunk_id", "chunk_document", "document_id",
        "document_content", "document_source", "chunk_text", "chunk_embedding"}


def test_a_home_with_no_old_graph_is_left_alone() -> None:
    driver = _Driver(lambda query, parameters: [])

    assert move_home_graph(driver) is False

    assert len(driver.sent) == 1 and "SHOW CONSTRAINTS" in driver.sent[0][0]


_NODES = [
    {"id": "d1", "labels": ["DocNode", "Document"], "properties": {"id": "d1", "label": "a.pdf"}},
    {"id": "u1", "labels": ["DocNode", "TextChunk"], "properties": {"id": "u1", "text": "Hi"}},
]
_EDGES = [{"id": "e1", "type": "CONTAINS", "start": "d1", "end": "u1",
           "properties": {"id": "e1"}}]
_CHUNKS = [{"id": "d1:0", "properties": {"id": "d1:0", "text": "Hi", "embedding": [0.1, 0.2]}}]
_COVERS = [{"chunk": "d1:0", "node": "u1"}]


def _source(query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    if "COVERS" in query:
        return _COVERS
    if "-[r]->" in query:
        return _EDGES
    if "(c:Chunk)" in query:
        return _CHUNKS
    if "(n:DocNode)" in query:
        return _NODES
    return []


def _target(counts: dict[str, int]) -> Answer:
    def answer(query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        for key, value in counts.items():
            if f"AS {key}" in query:
                return [{key: value}]
        return []
    return answer


def test_a_folder_s_graph_is_copied_under_its_workspace_and_counted() -> None:
    scope = WorkspaceScope("0a1b2c")
    source = _Driver(_source)
    target = _Driver(_target({"nodes": 2, "relationships": 1, "chunks": 1, "covers": 1,
                              "size": 2}))

    counts = copy_graph(source, target, scope, batch=1)

    assert counts == workspace_move.Counts(nodes=2, relationships=1, chunks=1, covers=1)
    # The folder's graph is only read.
    assert not any(word in q for q in source.queries()
                   for word in ("CREATE", "MERGE", "SET", "DELETE", "DROP"))
    written = [q for q in target.queries() if "MERGE" in q]
    assert written and all(not unscoped_patterns(q) for q in written)
    assert any(":Document" in q and "W_0a1b2c" in q for q in written)
    assert any("C_0a1b2c" in q and "setNodeVectorProperty" in q for q in target.queries())
    vectors = [p for q, p in target.sent if "setNodeVectorProperty" in q]
    assert vectors[0]["rows"][0]["vector"] == [0.1, 0.2]
    assert "embedding" not in vectors[0]["rows"][0]["properties"]
    assert any("CREATE VECTOR INDEX" in q and p["name"] == "chunk_embedding_0a1b2c"
               for q, p in target.sent)


def test_a_copy_whose_counts_differ_is_not_taken_as_moved() -> None:
    target = _Driver(_target({"nodes": 1, "relationships": 1, "chunks": 1, "covers": 1}))

    with pytest.raises(MoveMismatch):
        copy_graph(_Driver(_source), target, WorkspaceScope("0a1b2c"))

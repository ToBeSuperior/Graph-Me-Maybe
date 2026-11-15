"""A workspace's queries name its own labels: its units carry `W_<id>`, its passages `C_<id>`,
so a query reaches nothing of another workspace. Invented queries in the shapes the storage
adapters write."""
from __future__ import annotations

import pytest

from graphme.adapters.storage.workspace_scope import WorkspaceScope, unscoped_patterns

SCOPE = WorkspaceScope("4a8cf515")


def test_its_labels_and_index_names() -> None:
    assert (SCOPE.node_label, SCOPE.chunk_label) == ("W_4a8cf515", "C_4a8cf515")
    assert (SCOPE.vector_index, SCOPE.text_index) == (
        "chunk_embedding_4a8cf515", "chunk_text_4a8cf515")


@pytest.mark.parametrize(("query", "scoped"), [
    ("MATCH (n:DocNode {id: $id}) RETURN n",
     "MATCH (n:DocNode:W_4a8cf515 {id: $id}) RETURN n"),
    ("MATCH (d:Document)-[:SAME_AS]->(e:DocNode:ExternalDocument) RETURN d",
     "MATCH (d:Document:W_4a8cf515)-[:SAME_AS]->(e:DocNode:ExternalDocument:W_4a8cf515) "
     "RETURN d"),
    ("MATCH (c:Chunk {document_id: $d})-[r:COVERS]->(n) DETACH DELETE c",
     "MATCH (c:Chunk:C_4a8cf515 {document_id: $d})-[r:COVERS]->(n) DETACH DELETE c"),
    ("MATCH (d:Document) WHERE NOT EXISTS { MATCH (c:Chunk {rule: 'x:y'}) } RETURN d",
     "MATCH (d:Document:W_4a8cf515) WHERE NOT EXISTS { MATCH (c:Chunk:C_4a8cf515 "
     "{rule: 'x:y'}) } RETURN d"),
    ("UNWIND $rows AS row MERGE (n:DocNode {id: row.id}) SET n:Table SET n += row.p",
     "UNWIND $rows AS row MERGE (n:DocNode:W_4a8cf515 {id: row.id}) SET n:Table SET n += row.p"),
])
def test_a_query_is_held_to_the_workspace(query: str, scoped: str) -> None:
    assert SCOPE.cypher(query) == scoped
    assert unscoped_patterns(scoped) == []


def test_a_query_already_scoped_is_left_as_it_is() -> None:
    once = SCOPE.cypher("MATCH (n:DocNode {id: $id}) RETURN n")
    assert SCOPE.cypher(once) == once


def test_an_unscoped_pattern_is_found() -> None:
    assert unscoped_patterns("MATCH (n:DocNode) MATCH (m:DocNode:W_x) RETURN n") == [
        "(n:DocNode"]

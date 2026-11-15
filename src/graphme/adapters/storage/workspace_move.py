"""Graphs from before every workspace was in the home's one Neo4j, moved into it once.

The home's own graph is already there, under no workspace's label: its units become the default
workspace's (`W_default`), its passages `C_default`, a batch at a time, and the global
constraints and indexes it was searched by are dropped -- last, since the old `doc_node_id`
constraint is what says the move is not done. A workspace whose graph is in its own folder
(`neo4j-data/`) is read from that folder's Neo4j and written under its scope, vectors and all;
what was written is counted against what was read. Nothing is read again from a file, and
nothing is sent to a model.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from neo4j import Driver
from neo4j_graphrag.indexes import create_vector_index

from graphme.adapters.export.neo4j import CHUNK_LABEL, NODE_LABEL
from graphme.adapters.storage.graph_rag import VECTOR_PROPERTY
from graphme.adapters.storage.neo4j_space import (
    Neo4jGraphSpace,
    _write_nodes,
    _write_relationships,
)
from graphme.adapters.storage.workspace_scope import DEFAULT_SCOPE, WorkspaceScope

OLD_SCHEMA: tuple[tuple[str, str], ...] = (
    ("CONSTRAINT", "doc_node_id"), ("INDEX", "doc_node_document"),
    ("CONSTRAINT", "chunk_id"), ("INDEX", "chunk_document"),
    ("INDEX", "document_id"), ("INDEX", "document_content"), ("INDEX", "document_source"),
    ("INDEX", "chunk_text"), ("INDEX", "chunk_embedding"),
)
"""The global constraints and indexes every graph had before workspaces had their own; the
constraint first in it says a graph still holds nodes of no workspace."""
MOVE_BATCH = 10_000
"""Nodes relabelled, or rows copied, in one transaction."""


class MoveMismatch(RuntimeError):
    """What was written under the workspace is not what its folder's graph holds."""


@dataclass(frozen=True)
class Counts:
    nodes: int
    relationships: int
    chunks: int
    covers: int


def move_home_graph(driver: Driver, *, batch: int = MOVE_BATCH) -> bool:
    """The home's graph from before workspaces made the default workspace's; whether there was
    one. Safe to run again: only nodes of no workspace are relabelled, and nothing is dropped
    that is not there."""
    marker = OLD_SCHEMA[0][1]
    if not driver.execute_query(
            "SHOW CONSTRAINTS YIELD name WHERE name = $name RETURN name", name=marker).records:
        return False
    for label, mark, own in ((NODE_LABEL, "W_", DEFAULT_SCOPE.node_label),
                             (CHUNK_LABEL, "C_", DEFAULT_SCOPE.chunk_label)):
        while driver.execute_query(
                f"MATCH (n:{label}) WHERE NOT any(l IN labels(n) WHERE l STARTS WITH '{mark}') "
                f"WITH n LIMIT $batch SET n:{own} RETURN count(n) AS done",
                batch=batch).records[0]["done"]:
            pass
    Neo4jGraphSpace(driver, scope=DEFAULT_SCOPE)  # its own constraints and indexes
    index_vectors(driver, DEFAULT_SCOPE)
    for kind, name in OLD_SCHEMA:
        driver.execute_query(f"DROP {kind} {name} IF EXISTS")
    return True


def index_vectors(driver: Driver, scope: WorkspaceScope) -> None:
    """The workspace's vector index, at the size its passages were embedded at, when they were:
    the vectors stay on the passages, the index over them is the workspace's own."""
    found = driver.execute_query(
        f"MATCH (c:{scope.chunk_label}) WHERE c.{VECTOR_PROPERTY} IS NOT NULL "
        f"RETURN size(c.{VECTOR_PROPERTY}) AS size LIMIT 1").records
    if found:
        create_vector_index(driver, scope.vector_index, label=scope.chunk_label,
                            embedding_property=VECTOR_PROPERTY,
                            dimensions=int(found[0]["size"]), similarity_fn="cosine")


_NODES = (f"MATCH (n:{NODE_LABEL}) "
          "RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties")
_RELATIONSHIPS = (f"MATCH (a:{NODE_LABEL})-[r]->(b:{NODE_LABEL}) RETURN r.id AS id, "
                  "type(r) AS type, a.id AS start, b.id AS end, properties(r) AS properties")
_CHUNKS = f"MATCH (c:{CHUNK_LABEL}) RETURN c.id AS id, properties(c) AS properties"
_COVERS = (f"MATCH (c:{CHUNK_LABEL})-[:COVERS]->(n:{NODE_LABEL}) "
           "RETURN c.id AS chunk, n.id AS node")


def copy_graph(source: Driver, target: Driver, scope: WorkspaceScope, *,
               batch: int = MOVE_BATCH) -> Counts:
    """Everything a workspace folder's graph (`source`, only read) holds, written into the one
    Neo4j (`target`) under `scope`: units with their labels, their relationships, passages with
    their vectors, what each covers -- then the workspace's indexes. Written by id (MERGE), so
    a copy cut short is finished by the next. MoveMismatch when what is there afterwards is not
    what was read."""
    Neo4jGraphSpace(target, scope=scope)  # its constraints first: MERGE finds by them
    read = Counts(
        nodes=_copy(source, target, _NODES, batch,
                    lambda tx, rows: _write_nodes(tx, scope, rows)),
        relationships=_copy(source, target, _RELATIONSHIPS, batch,
                            lambda tx, rows: _write_relationships(tx, scope, rows)),
        chunks=_copy(source, target, _CHUNKS, batch,
                     lambda tx, rows: _write_chunks(tx, scope, rows)),
        covers=_copy(source, target, _COVERS, batch,
                     lambda tx, rows: _write_covers(tx, scope, rows)),
    )
    there = Counts(**{key: target.execute_query(scope.cypher(query)).records[0][key]
                      for key, query in (
                          ("nodes", f"MATCH (n:{NODE_LABEL}) RETURN count(n) AS nodes"),
                          ("relationships", f"MATCH (:{NODE_LABEL})-[r]->(:{NODE_LABEL}) "
                                            "RETURN count(r) AS relationships"),
                          ("chunks", f"MATCH (c:{CHUNK_LABEL}) RETURN count(c) AS chunks"),
                          ("covers", f"MATCH (:{CHUNK_LABEL})-[r:COVERS]->(:{NODE_LABEL}) "
                                     "RETURN count(r) AS covers"))})
    if there != read:
        raise MoveMismatch(f"read {read}, the workspace holds {there}")
    index_vectors(target, scope)
    return read


def _copy(source: Driver, target: Driver, query: str, batch: int, write: Any) -> int:
    """Each `batch` rows `query` reads from `source` written by `write` in one transaction of
    `target`'s; how many rows."""
    done = 0
    with target.session() as session:
        for rows in _batches(source, query, batch):
            session.execute_write(write, rows)
            done += len(rows)
    return done


def _batches(driver: Driver, query: str, size: int) -> Iterator[list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    with driver.session() as session:
        for record in session.run(query):
            rows.append(dict(record))
            if len(rows) == size:
                yield rows
                rows = []
    if rows:
        yield rows


def _write_chunks(tx: Any, scope: WorkspaceScope, rows: list[dict[str, Any]]) -> None:
    """Passages by id, each vector set as the embedding sets one (`setNodeVectorProperty`)."""
    given = []
    for row in rows:
        properties = dict(row["properties"])
        given.append({"id": row["id"], "vector": properties.pop(VECTOR_PROPERTY, None),
                      "properties": properties})
    tx.run(scope.cypher(
        f"UNWIND $rows AS row MERGE (c:{CHUNK_LABEL} {{id: row.id}}) SET c += row.properties "
        "WITH c, row WHERE row.vector IS NOT NULL "
        "CALL db.create.setNodeVectorProperty(c, $property, row.vector)"),
        rows=given, property=VECTOR_PROPERTY)


def _write_covers(tx: Any, scope: WorkspaceScope, rows: list[dict[str, Any]]) -> None:
    tx.run(scope.cypher(
        f"UNWIND $rows AS row MATCH (c:{CHUNK_LABEL} {{id: row.chunk}}), "
        f"(n:{NODE_LABEL} {{id: row.node}}) MERGE (c)-[:COVERS]->(n)"), rows=rows)

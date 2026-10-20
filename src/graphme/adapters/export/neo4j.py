"""The graph in the shape Neo4j stores: labels, relationship types, scalar properties.

Every node carries the shared label `DocNode`, which holds the uniqueness constraint on
`id`, and its type as a second label (`text_chunk` -> `TextChunk`). An edge type is
already a valid relationship type. Neo4j keeps only scalars and arrays of scalars, so
nested values are JSON strings and nulls are left out -- setting a property to null
deletes it. Node metadata becomes `meta_<key>`, so it can never shadow a core field.

Only the four published relationship types leave (`application/final_edges.py`): the
outline and the file's own nesting arrive here as one `PARENT_OF` per node.

`graph_records` is the same records the driver hands back; `to_cypher` is a script of the
graph alone that
runs in `cypher-shell` or Neo4j Browser without a connection from here, and MERGEs on
ids so running it twice changes nothing.

Several documents in one graph are the graph space's (`adapters/storage/neo4j_space.py`),
and `records_cypher` writes what it holds the same way.
"""
from __future__ import annotations

import json
from typing import Any

from graphme.application.final_edges import final_edges
from graphme.domain.models import ExtractedDocument, GraphEdge, GraphNode, SourceLocator

NODE_LABEL = "DocNode"
CHUNK_LABEL = "Chunk"
"""A passage a question is searched by: beside the graph, never a `DocNode` of it."""
SCHEMA = (
    f"CREATE CONSTRAINT doc_node_id IF NOT EXISTS FOR (n:{NODE_LABEL}) REQUIRE n.id IS UNIQUE",
    # A document is replaced, deleted and cut by its id; without these every one of those
    # reads every node in the space.
    f"CREATE INDEX doc_node_document IF NOT EXISTS FOR (n:{NODE_LABEL}) ON (n.document_id)",
    f"CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:{CHUNK_LABEL}) REQUIRE c.id IS UNIQUE",
    f"CREATE INDEX chunk_document IF NOT EXISTS FOR (c:{CHUNK_LABEL}) ON (c.document_id)",
    # A document reached by its id -- a link followed, a likeness written -- found by it, not
    # by reading every document.
    "CREATE INDEX document_id IF NOT EXISTS FOR (d:Document) ON (d.id)",
    # A file already in the space is known by its content before it is read again.
    "CREATE INDEX document_content IF NOT EXISTS FOR (d:Document) ON (d.content_hash)",
    # What was read from a file before, found when the file is read again.
    "CREATE INDEX document_source IF NOT EXISTS FOR (d:Document) ON (d.source_path)",
)
"""The constraints and indexes every graph space has, live or exported; the vector and fulltext
indexes are added beside them at the size and analyzer they were made with."""


def node_label(node_type: str) -> str:
    return "".join(part.capitalize() for part in node_type.split("_"))


def _scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        kinds = {type(item) for item in value}
        # Neo4j stores a list only when every element is one type. Integers beside floats
        # are one measurement written two ways, so they become floats; anything else mixed
        # is kept whole as JSON rather than failing the statement that sets it.
        if kinds <= {int, float} and float in kinds:
            return [float(item) for item in value]
        if len(kinds) <= 1 and kinds <= {str, int, float, bool}:
            return list(value)
    return json.dumps(value, ensure_ascii=False)


def _properties(core: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    merged = {**core, **{f"meta_{key}": value for key, value in metadata.items()}}
    return {key: _scalar(value) for key, value in merged.items() if value is not None}


def _where(locator: SourceLocator | None) -> dict[str, Any]:
    if locator is None:
        return {}
    return {
        "file_name": locator.file_name,
        "locator": locator.label,
        "page": locator.page,
        "sheet": locator.sheet,
        "slide": locator.slide,
        "cell_range": locator.cell_range,
        "bbox": locator.bbox,
        "source_part": locator.source_part,
        "locator_metadata": locator.metadata or None,
    }


def _node(node: GraphNode) -> dict[str, Any]:
    core = {
        "id": node.id,
        "document_id": node.document_id,
        "type": node.type.value,
        "label": node.label,
        "text": node.text,
        "content_hash": node.content_hash,
        "retrievable": node.retrievable,
        **_where(node.locator),
    }
    return {
        "id": node.id,
        "labels": [NODE_LABEL, node_label(node.type.value)],
        "properties": _properties(core, node.metadata),
    }


def relationship_record(edge: GraphEdge) -> dict[str, Any]:
    core = {"id": edge.id, "document_id": edge.document_id, **_where(edge.locator)}
    return {
        "id": edge.id,
        "type": edge.type.value,
        "start": edge.source_id,
        "end": edge.target_id,
        "properties": _properties(core, edge.metadata),
    }


def graph_records(extracted: ExtractedDocument) -> dict[str, list[dict[str, Any]]]:
    return {
        "nodes": [_node(node) for node in extracted.nodes],
        # Four relationship types leave here, whatever this side used to decide them.
        "relationships": [relationship_record(edge) for edge in final_edges(extracted)],
    }


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    escaped = (
        str(value).replace("\\", "\\\\").replace("'", "\\'")
        .replace("\n", "\\n").replace("\r", "\\r")
    )
    return f"'{escaped}'"


def _map(properties: dict[str, Any]) -> str:
    # A backtick inside a quoted name is written twice, or the name ends there.
    return "{" + ", ".join(
        f"`{k.replace('`', '``')}`: {_literal(v)}" for k, v in properties.items()
    ) + "}"


def to_cypher(extracted: ExtractedDocument) -> str:
    return records_cypher(
        graph_records(extracted),
        f"// {extracted.document.format.value} document {extracted.document.id}",
    )


STATEMENT_CHARS = 200_000
"""How long one statement of a script may grow. Rows of one kind share a statement -- one
statement a row took a space half an hour to load -- but a passage's vector is tens of
kilobytes... which is why passages and their vectors never go in a script: the server
parses every literal, and a space's vectors ran a 1 GB server out of memory. They leave
through `neo4j_import` (Neo4j's bulk import) instead."""


def records_cypher(records: dict[str, list[dict[str, Any]]], heading: str) -> str:
    """A script for records in the exporter's shape: one document's, or the graph space's.

    Rows of one kind go in one `UNWIND` statement, and every write MERGEs, so a script
    loaded twice leaves what loading it once did."""
    lines = [
        heading,
        f"{SCHEMA[0]};",
    ]
    by_labels: dict[str, list[str]] = {}
    for node in records["nodes"]:
        extra = "".join(f":{label}" for label in node["labels"][1:])
        by_labels.setdefault(extra, []).append(
            f"{{id: {_literal(node['id'])}, properties: {_map(node['properties'])}}}"
        )
    for extra, rows in by_labels.items():
        lines += _unwind(
            rows,
            f"MERGE (n:{NODE_LABEL} {{id: row.id}})"
            + (f" SET n{extra}" if extra else "")
            + " SET n += row.properties;",
        )
    by_type: dict[str, list[str]] = {}
    for rel in records["relationships"]:
        props = {k: v for k, v in rel["properties"].items() if k != "id"}
        by_type.setdefault(rel["type"], []).append(
            f"{{id: {_literal(rel['id'])}, start: {_literal(rel['start'])}, "
            f"end: {_literal(rel['end'])}, properties: {_map(props)}}}"
        )
    for kind, rows in by_type.items():
        lines += _unwind(
            rows,
            f"MATCH (a:{NODE_LABEL} {{id: row.start}}), (b:{NODE_LABEL} {{id: row.end}}) "
            f"MERGE (a)-[r:{kind} {{id: row.id}}]->(b) SET r += row.properties;",
        )
    return "\n".join(lines) + "\n"


def _unwind(rows: list[str], body: str) -> list[str]:
    """`UNWIND [rows] AS row <body>`, cut into statements of at most `STATEMENT_CHARS`."""
    statements: list[str] = []
    batch: list[str] = []
    size = 0
    for row in rows:
        if batch and size + len(row) > STATEMENT_CHARS:
            statements.append(f"UNWIND [{', '.join(batch)}] AS row {body}")
            batch, size = [], 0
        batch.append(row)
        size += len(row) + 2
    if batch:
        statements.append(f"UNWIND [{', '.join(batch)}] AS row {body}")
    return statements


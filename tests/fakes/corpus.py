"""Whole-corpus helpers the tests check the incremental and streaming paths against."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from graphme.adapters.export.neo4j import CHUNK_LABEL
from graphme.adapters.export.neo4j_import import (
    ARRAY_DELIMITER,
    CorpusRows,
    NodeRow,
    RelationshipRow,
)
from graphme.application.corpus_links import Mention, identity_of, link_mentions
from graphme.domain.enums import NodeType
from graphme.domain.models import ExtractedDocument, GraphEdge


def corpus_links(documents: Sequence[ExtractedDocument]) -> tuple[GraphEdge, ...]:
    """A `SAME_AS` edge from every mention to every other document it names.

    A document loaded twice -- the same file uploaded beside the sample it came from -- has
    one id, and is one document here.
    """
    documents = list({document.document.id: document for document in documents}.values())
    return link_mentions(
        tuple(
            Mention(node_id=node.id, document_id=document.document.id, name=node.label)
            for document in documents
            for node in document.nodes
            if node.type is NodeType.EXTERNAL_DOCUMENT
        ),
        tuple(identity_of(document) for document in documents),
    )


def rows_of(records: dict[str, list[dict[str, Any]]]) -> CorpusRows:
    """Records already held as rows, the shape the bundle is written from."""

    def nodes() -> Iterable[NodeRow]:
        for node in records["nodes"]:
            yield ARRAY_DELIMITER.join(node["labels"]), node["id"], node["properties"]
        for chunk in records.get("chunks", []):
            yield CHUNK_LABEL, chunk["id"], chunk["properties"]

    def relationships() -> Iterable[RelationshipRow]:
        for rel in records["relationships"]:
            yield rel["type"], rel["start"], rel["end"], {**rel["properties"], "id": rel["id"]}
        for cover in records.get("covers", []):
            yield "COVERS", cover["chunk"], cover["node"], {}

    return CorpusRows(nodes=nodes, relationships=relationships,
                      indexes=records.get("indexes", []),
                      documents=sum("Document" in node["labels"] for node in records["nodes"]))


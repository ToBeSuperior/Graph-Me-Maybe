"""Invented documents, built in memory: no file, nothing read, nothing measured."""
from __future__ import annotations

from pathlib import Path

from graphme.domain.enums import DocumentFormat, EdgeType, EvidenceKind, NodeType
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    SourceLocator,
)


def _node(
    doc: str, node_id: str, node_type: NodeType, label: str, file_name: str | None = None
) -> GraphNode:
    return GraphNode(
        id=node_id, document_id=doc, type=node_type, label=label,
        text=None if node_type is not NodeType.TEXT_CHUNK else label,
        locator=SourceLocator(file_name=file_name or f"{doc}.pdf", label=label),
        retrievable=False,
    )


def named_document(
    doc: str,
    file_name: str,
    *,
    names: tuple[str, ...] = (),
    title: str | None = None,
) -> ExtractedDocument:
    """A document whose file is `file_name`, naming `names`, read under `title` if given."""
    # Every node of a real document names the file it came from, the root above all.
    root = _node(doc, doc, NodeType.DOCUMENT, file_name, file_name)
    nodes = [root]
    edges = []
    for index, name in enumerate(names):
        nodes.append(_node(doc, f"{doc}-ext{index}", NodeType.EXTERNAL_DOCUMENT, name))
    if title is not None:
        nodes += [
            _node(doc, f"{doc}-title", NodeType.TEXT_CHUNK, title),
            _node(doc, f"{doc}-body", NodeType.TEXT_CHUNK, "body text"),
            _node(doc, f"{doc}-page", NodeType.TEXT_CHUNK, "7"),
        ]
        edges.append(
            GraphEdge(
                id=f"{doc}-outline", document_id=doc, source_id=f"{doc}-title",
                target_id=f"{doc}-body", type=EdgeType.OUTLINE_PARENT,
            )
        )
    edges += [
        GraphEdge(
            id=f"{doc}-contains-{node.id}", document_id=doc, source_id=doc,
            target_id=node.id, type=EdgeType.CONTAINS,
        )
        for node in nodes[1:]
    ]
    return ExtractedDocument(
        document=DocumentRecord(
            id=doc, file_name=file_name, content_hash=(doc * 64)[:64],
            format=DocumentFormat.PDF, size_bytes=1, source_path=Path(file_name),
        ),
        nodes=tuple(nodes),
        edges=tuple(edges),
        evidence=(),
    )


def units_document(doc: str, file_name: str, units: list[tuple[str, NodeType, str]],
                   edges: list[tuple[str, str, EdgeType]]) -> ExtractedDocument:
    """A DOCX holding `units` (id, type, text) under its root, joined also by `edges`."""
    locator = SourceLocator(file_name=file_name, label=file_name)
    nodes = [
        GraphNode(id=doc, document_id=doc, type=NodeType.DOCUMENT, label=file_name,
                  locator=locator, retrievable=False)
    ]
    evidence = []
    for node_id, node_type, text in units:
        retrievable = node_type is NodeType.TEXT_CHUNK
        nodes.append(
            GraphNode(id=node_id, document_id=doc, type=node_type, label=text,
                      text=text if retrievable else None, locator=locator,
                      retrievable=retrievable)
        )
        if retrievable:
            evidence.append(
                EvidenceRecord(id=f"{node_id}-ev", document_id=doc, node_id=node_id,
                               kind=EvidenceKind.SOURCE_TEXT, locator=locator, text=text)
            )
    all_edges = [
        GraphEdge(id=f"{doc}-c-{node_id}", document_id=doc, source_id=doc, target_id=node_id,
                  type=EdgeType.CONTAINS)
        for node_id, _, _ in units
    ] + [
        GraphEdge(id=f"{source}-{kind.value}-{target}", document_id=doc, source_id=source,
                  target_id=target, type=kind)
        for source, target, kind in edges
    ]
    return ExtractedDocument(
        document=DocumentRecord(id=doc, file_name=file_name, content_hash=(doc * 64)[:64],
                                format=DocumentFormat.DOCX, size_bytes=1,
                                source_path=Path(file_name)),
        nodes=tuple(nodes), edges=tuple(all_edges), evidence=tuple(evidence),
    )

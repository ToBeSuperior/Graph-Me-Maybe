"""A figure the outline drops takes the words drawn on it.

A header icon on every page had a word stamped on it, and the icon owned the word it covers.
The outline dropped the icon as decoration -- it is shown the icon's crop, word and all -- and
the word, handed to the document, read at the top of the document: one on every page.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from graphme.application.document_graph import DocumentGraphBuilder
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    HierarchyDecision,
    HierarchyDerivation,
    SourceLocator,
)


def _report() -> ExtractedDocument:
    where = SourceLocator(file_name="r.pdf", label="r.pdf", page=1)
    nodes = (
        GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r.pdf", locator=where,
                  retrievable=False),
        GraphNode(id="icon", document_id="d", type=NodeType.IMAGE, label="Image 1",
                  locator=where, retrievable=False),
        GraphNode(id="stamp", document_id="d", type=NodeType.TEXT_CHUNK, label="internal",
                  text="internal", locator=where, retrievable=False),
        GraphNode(id="label", document_id="d", type=NodeType.TEXT_CHUNK, label="draft",
                  text="draft", locator=where, retrievable=False,
                  metadata={"part_of_figure": "icon"}),
        GraphNode(id="body", document_id="d", type=NodeType.TEXT_CHUNK, label="Pump results",
                  text="Pump results", locator=where, retrievable=False),
    )
    return ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r.pdf", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r.pdf")),
        nodes=nodes,
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d",
                              source_id="icon" if n.id == "stamp" else "d", target_id=n.id,
                              type=EdgeType.CONTAINS) for n in nodes[1:]),
        evidence=(),
    )


class _Drops:
    def derive(self, *, extracted: Any, source_path: str, on_page: Any = None) -> Any:
        return HierarchyDerivation(decisions=(
            HierarchyDecision(subject_id="icon", status="drop", confidence=0.9,
                              rationale="decoration"),
            HierarchyDecision(subject_id="body", status="root", confidence=0.9,
                              rationale="content")))


def test_the_words_on_a_dropped_figure_go_with_it() -> None:
    builder = DocumentGraphBuilder(extractors={}, hierarchy_provider=_Drops())  # type: ignore[arg-type]
    read = builder.read_outline(_report(), Path("r.pdf"))
    kept = {n.id for n in read.nodes}
    assert "icon" not in kept and "stamp" not in kept and "label" not in kept
    assert "body" in kept

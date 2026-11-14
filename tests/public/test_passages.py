"""`graphme.passages`: a document read on its own is cut as the graph space would cut it, each
passage saying where it is from. Invented document."""
from __future__ import annotations

from pathlib import Path

import graphme
from graphme.application.graph_chunks import GraphUnit, chunks
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    SourceLocator,
)


def _node(node_id: str, kind: NodeType, text: str | None, page: int, top: float) -> GraphNode:
    return GraphNode(id=node_id, document_id="d", type=kind, label=node_id, text=text,
                     locator=SourceLocator(file_name="notice.pdf", label=node_id, page=page,
                                           bbox=(0.1, top, 0.9, top + 0.05)),
                     retrievable=False)  # evidence aside: passages do not look at it


def _edge(kind: EdgeType, source: str, target: str) -> GraphEdge:
    return GraphEdge(id=f"{kind.value}-{source}-{target}", document_id="d", source_id=source,
                     target_id=target, type=kind)


def _document() -> ExtractedDocument:
    nodes = (
        _node("d", NodeType.DOCUMENT, None, 1, 0.0),
        _node("h", NodeType.TEXT_CHUNK, "Invented heading", 1, 0.1),
        _node("p", NodeType.TEXT_CHUNK, "An invented paragraph under it.", 1, 0.2),
        _node("t", NodeType.TABLE, "a | b\n1 | 2", 2, 0.1),
        _node("c", NodeType.VLM_ENRICHED_CAPTION, "A two-column invented table.", 2, 0.1),
        _node("q", NodeType.TEXT_CHUNK, "A closing line.", 2, 0.5),
    )
    edges = (
        *(_edge(EdgeType.CONTAINS, "d", n) for n in ("h", "p", "t", "q")),
        _edge(EdgeType.CONTAINS, "t", "c"),
        _edge(EdgeType.CAPTION_OF, "c", "t"),
        _edge(EdgeType.OUTLINE_PARENT, "h", "p"),
        _edge(EdgeType.OUTLINE_PARENT, "h", "t"),
    )
    return ExtractedDocument(
        document=DocumentRecord(id="d", file_name="notice.pdf", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("notice.pdf")),
        nodes=nodes, edges=edges, evidence=())


def test_passages_are_the_cut_the_graph_space_makes() -> None:
    as_the_space_reads_them = [  # what its query gives for this document
        GraphUnit(id="h", parent=None, text="Invented heading", order=(1.0, 0.1)),
        GraphUnit(id="p", parent="h", text="An invented paragraph under it.", order=(1.0, 0.2)),
        GraphUnit(id="t", parent="h", text="a | b\n1 | 2\nA two-column invented table.",
                  order=(2.0, 0.1), attached=("c",)),
        GraphUnit(id="q", parent=None, text="A closing line.", order=(2.0, 0.5)),
    ]

    got = graphme.passages(_document())

    assert [(p.text, p.node_ids) for p in got] == [
        (c.text, c.member_ids) for c in chunks("d", as_the_space_reads_them)]


def test_a_passage_says_where_it_is_from() -> None:
    got = graphme.passages(_document())
    holding_the_table = next(p for p in got if "t" in p.node_ids)

    assert holding_the_table.metadata["file"] == "notice.pdf"
    assert holding_the_table.metadata["format"] == "pdf"
    assert 2 in holding_the_table.metadata["pages"]
    assert "A two-column invented table." in holding_the_table.text
    assert holding_the_table.to_dict()["node_ids"] == list(holding_the_table.node_ids)

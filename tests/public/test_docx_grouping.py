"""A Word file's paragraphs are grouped too.

Word hands over whole paragraphs, so nothing was ever printed apart. But an author who
wraps a sentence by hand -- Enter at the end of every line -- writes one sentence as two
paragraphs, and at a page break the outline reads the second as a heading of its own.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.vision.text_grouping import RenderedUnitGrouper
from graphme.application.unit_grouping import JOINED_FROM
from graphme.domain.enums import DocumentFormat, EdgeType, EvidenceKind, NodeType
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    SourceLocator,
    UnitGroupingResult,
    UnitLabel,
)

DOC = "memo"
WHERE = SourceLocator(file_name="memo.docx", label="memo.docx")


def _paragraph(node_id: str, text: str, body_index: int) -> GraphNode:
    return GraphNode(
        id=node_id, document_id=DOC, type=NodeType.TEXT_CHUNK, label=text, locator=WHERE,
        text=text, metadata={"body_index": body_index},
    )


def _memo(*paragraphs: GraphNode) -> ExtractedDocument:
    root = GraphNode(id=DOC, document_id=DOC, type=NodeType.DOCUMENT, label="memo.docx",
                     locator=WHERE, retrievable=False)
    return ExtractedDocument(
        document=DocumentRecord(id=DOC, content_hash="d" * 64, file_name="memo.docx",
                                format=DocumentFormat.DOCX, source_path="memo.docx",
                                size_bytes=1),
        nodes=(root, *paragraphs),
        edges=tuple(GraphEdge(id=f"contains:{DOC}->{p.id}", document_id=DOC, source_id=DOC,
                              target_id=p.id, type=EdgeType.CONTAINS) for p in paragraphs),
        evidence=tuple(EvidenceRecord(id=f"evidence:{p.id}", document_id=DOC, node_id=p.id,
                                      kind=EvidenceKind.SOURCE_TEXT, locator=WHERE, text=p.text)
                       for p in paragraphs),
    )


def _page(index: int, *drawn: tuple[int, str]) -> RenderedPage:
    """A laid-out page: each paragraph (counted from zero, as the renderer does) one line."""
    return RenderedPage(
        index=index, width=600, height=800, png=f"page {index}".encode(),
        text_runs=tuple({"path": [body], "story": "body", "instance": "body", "text": text,
                         "x": 50, "y": 700 if index == 1 else 60, "w": 400, "h": 20}
                        for body, text in drawn),
    )


class _Pages:
    def __init__(self, *pages: RenderedPage) -> None:
        self.pages = pages

    def render(self, source: Path, document_format: DocumentFormat) -> list[RenderedPage]:
        return list(self.pages)


class _Joins:
    """Says the two paragraphs are one sentence, and records the pages it was shown."""

    def __init__(self, *together: str) -> None:
        self.together, self.shown = together, []

    def group_page_units(self, *, request: Any, images: list[bytes]) -> UnitGroupingResult:
        self.shown.append(images)
        asked = [unit.id for unit in request.units if unit.id in self.together]
        return UnitGroupingResult(labels=tuple(
            UnitLabel(unit_id=unit_id, group="s", order=order)
            for order, unit_id in enumerate(asked, start=1)
        ) if len(asked) > 1 else ())


def test_a_sentence_wrapped_by_hand_across_a_page_break_becomes_one_paragraph() -> None:
    memo = _memo(
        _paragraph("head", "Findings", 1),
        _paragraph("ends", "the cover is pressed in further, so the", 2),
        _paragraph("goes", "drain sits deeper in the tray", 3),
    )
    vision = _Joins("ends", "goes")
    grouper = RenderedUnitGrouper(
        renderer=_Pages(_page(1, (0, "Findings"), (1, "the cover is pressed")),
                        _page(2, (2, "drain sits deeper"))),
        vision=vision,
    )

    grouped = grouper.group(extracted=memo, source=Path("memo.docx"))

    texts = [n.text for n in grouped.nodes if n.type is NodeType.TEXT_CHUNK]
    assert texts == ["Findings", "the cover is pressed in further, so the "
                     "drain sits deeper in the tray"]
    joined = next(n for n in grouped.nodes if n.id == "ends")
    assert joined.metadata[JOINED_FROM] == ["ends", "goes"]
    # The ask showed both pages: the sentence is read across the break.
    assert vision.shown == [[b"page 1", b"page 2"]]

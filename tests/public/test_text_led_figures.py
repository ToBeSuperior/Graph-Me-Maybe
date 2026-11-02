"""A figure drawn in vectors has no picture node: the page says which words are one figure, and
the words that lead it -- a chart's title -- take the figure's crop. That unit is read as the
figure it leads: described like any other visual, and shown to the outline with its crop.
Invented report."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from graphme.adapters.vision.hierarchy_scope import unit_crops
from graphme.application.visual_captions import caption_visuals
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    HierarchyUnit,
    SourceLocator,
    VisualCaption,
    VisualCaptionResult,
)

WHERE = SourceLocator(file_name="r.pdf", label="r.pdf", page=1)
LED = {CROP_ASSET_KEY: "a" * 64, "crop_widened_by": "figure_parts"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (240, 240, 235)).save(buffer, "PNG")
    return buffer.getvalue()


@dataclass
class _Page:
    index: int = 1
    width: int = 1000
    height: int = 1400
    png: bytes = b"\x89PNG\r\n\x1a\n"
    label: str | None = None


class _Crops:
    def read(self, asset_hash: str, suffix: str = "") -> bytes:
        return _png()


class _Captioner:
    def __init__(self) -> None:
        self.asked: list[tuple[str, ...]] = []

    def caption_visuals(self, *, request: Any, image: bytes,
                        unit_images: Any = None) -> VisualCaptionResult:
        self.asked.append(tuple(unit.id for unit in request.units))
        return VisualCaptionResult(captions=tuple(
            VisualCaption(subject_id=u.id, caption="pump hours rise each quarter", confidence=0.9)
            for u in request.units))


def _text(node_id: str, words: str, metadata: dict[str, Any]) -> GraphNode:
    return GraphNode(id=node_id, document_id="d", type=NodeType.TEXT_CHUNK, label=words,
                     text=words, locator=WHERE, retrievable=False, metadata=metadata)


def _report() -> ExtractedDocument:
    nodes = (
        _text("title", "Pump hours by quarter", LED),
        _text("axis", "Q1", {"part_of_figure": "title"}),
        # A crop alone is not a figure: a scan's words carry the crop they were read from.
        _text("body", "Every pump is inspected each 2,000 hours.", {CROP_ASSET_KEY: "b" * 64}),
    )
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r.pdf",
                     locator=WHERE, retrievable=False)
    return ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r.pdf", content_hash="c" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r.pdf")),
        nodes=(root, *nodes),
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d", source_id="d",
                              target_id=n.id, type=EdgeType.CONTAINS) for n in nodes),
        evidence=(),
    )


def test_a_figure_led_by_its_words_is_described_like_any_visual() -> None:
    captioner = _Captioner()

    described = caption_visuals(_report(), pages=(_Page(),), vision=captioner,  # type: ignore[arg-type]
                                asset_store=_Crops())  # type: ignore[arg-type]

    assert captioner.asked == [("title",)]
    caption = next(n for n in described.nodes if n.type is NodeType.VLM_ENRICHED_CAPTION)
    assert caption.metadata["caption_of"] == "title"
    described.validate_canonical_semantic_graph()


def test_the_outline_is_shown_the_figure_a_title_leads() -> None:
    report = _report()
    nodes = {node.id: node for node in report.nodes}
    units = [HierarchyUnit(id=n, label=nodes[n].label, scope="p1", order=i,
                           node_type=NodeType.TEXT_CHUNK)
             for i, n in enumerate(("title", "body"), start=1)]

    shown = unit_crops(units, nodes=nodes, asset_store=_Crops())  # type: ignore[arg-type]

    assert set(shown) == {"title"}


class _Unimpressed(_Captioner):
    def caption_visuals(self, *, request: Any, image: bytes,
                        unit_images: Any = None) -> VisualCaptionResult:
        return VisualCaptionResult(captions=tuple(
            VisualCaption(subject_id=u.id, caption="a heading", confidence=0.9, decorative=True)
            for u in request.units))


def test_words_leading_a_figure_are_never_dropped_as_decoration() -> None:
    # They are the file's own words; whether they are noise is the outline's question, not one
    # a crop of them can answer.
    described = caption_visuals(_report(), pages=(_Page(),), vision=_Unimpressed(),  # type: ignore[arg-type]
                                asset_store=_Crops())  # type: ignore[arg-type]

    kept = {node.id for node in described.nodes}
    assert {"title", "axis"} <= kept
    assert not any(n.type is NodeType.VLM_ENRICHED_CAPTION for n in described.nodes)

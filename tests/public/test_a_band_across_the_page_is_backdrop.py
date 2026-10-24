"""A picture laid edge to edge across a paper page is what the page is printed on.

A cover drew its title over a coloured band that ran the full width of the page. What a
picture covers belongs to it, so the band took the title, the subtitle and the logos printed
on it: the outline was never asked about the title, and the document hung from a picture.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from graphme.application.page_regions import PixelBox
from graphme.application.visual_crops import bind_page_visuals
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    SourceLocator,
)

BOXES = {
    "band": PixelBox(0, 0, 1000, 400),        # a colour band across the top of the page
    "title": PixelBox(150, 150, 850, 230),    # "Quarterly Pump Review", printed on the band
    "logo": PixelBox(880, 30, 960, 110),      # a small mark on the band
    "photo": PixelBox(100, 450, 600, 700),    # a photograph in the body
    "label": PixelBox(120, 470, 300, 500),    # "inlet valve", written on the photograph
    "wide": PixelBox(0, 800, 1000, 1200),     # a screenshot the width of the page, in the body
    "note": PixelBox(40, 820, 400, 860),      # "step 3", written on the screenshot
}
KINDS = {"band": NodeType.IMAGE, "title": NodeType.TEXT_CHUNK, "logo": NodeType.IMAGE,
         "photo": NodeType.IMAGE, "label": NodeType.TEXT_CHUNK, "wide": NodeType.IMAGE,
         "note": NodeType.TEXT_CHUNK}


@dataclass
class _Page:
    index: int = 1
    width: int = 1000
    height: int = 1400
    png: bytes = b"\x89PNG\r\n\x1a\n"


class _Placer:
    def box(self, node: GraphNode) -> PixelBox | None:
        return BOXES.get(node.id)


class _Store:
    def put(self, payload: bytes, *, suffix: str = "") -> str:
        return "c" * 64


def _bound(document_format: DocumentFormat) -> ExtractedDocument:
    where = SourceLocator(file_name="r", label="r", page=1)
    nodes = tuple(GraphNode(id=k, document_id="d", type=t, label=k,
                            text=None if t is NodeType.IMAGE else k, locator=where,
                            retrievable=False)
                  for k, t in KINDS.items())
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r", locator=where,
                      retrievable=False)
    extracted = ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r", content_hash="b" * 64,
                                format=document_format, size_bytes=1, source_path=Path("r")),
        nodes=(root, *nodes),
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d", source_id="d", target_id=n.id,
                              type=EdgeType.CONTAINS) for n in nodes),
        evidence=(),
    )
    return bind_page_visuals(extracted, page=_Page(), members=nodes, placer=_Placer(),
                             asset_store=_Store(), crop=lambda box: b"png")


def _owners(result: ExtractedDocument) -> dict[str, str]:
    return {e.target_id: e.source_id for e in result.edges if e.type is EdgeType.CONTAINS}


def test_a_band_across_a_paper_page_takes_nothing_printed_on_it() -> None:
    result = _bound(DocumentFormat.PDF)
    owners = _owners(result)
    assert owners["title"] == "d"
    assert owners["logo"] == "d"
    assert next(n for n in result.nodes if n.id == "band").metadata.get("background") is True
    # A picture in the body still holds the words written on it.
    assert owners["label"] == "photo"


def test_a_sheet_is_as_wide_as_what_is_on_it_so_its_widest_picture_is_no_backdrop() -> None:
    owners = _owners(_bound(DocumentFormat.XLSX))
    assert owners["title"] == "band"


def test_a_picture_the_width_of_the_page_in_its_body_is_content_and_keeps_what_is_on_it(
) -> None:
    """Edge to edge across, but standing in the body, off the page's top and foot: a wide
    screenshot, not what the page is printed on."""
    result = _bound(DocumentFormat.PDF)
    owners = _owners(result)
    assert owners["note"] == "wide"
    assert not next(n for n in result.nodes if n.id == "wide").metadata.get("background")

"""What lies outside a table's frame is not the table's, whatever it was grouped with.

The page was asked which units are one figure, and named a section heading printed above a
results table as part of it. The table then took everything inside the rectangle that
heading stretched it to -- the summary lines between the heading and the grid -- and the
outline put the heading and the summary under the table they introduce.
"""
from __future__ import annotations

from pathlib import Path

from graphme.application.figure_groups import widen_figures
from graphme.application.page_regions import PixelBox
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    HierarchyFigureGroup,
    SourceLocator,
)

BOXES = {
    "heading": PixelBox(80, 200, 520, 232),    # "Durability by alloy"
    "summary": PixelBox(90, 246, 900, 278),    # "- The new alloy kept its finish."
    "grid": PixelBox(100, 300, 1000, 700),     # the results table
    "band": PixelBox(110, 702, 400, 720),      # a band row between the table's two parts
    "rows": PixelBox(100, 722, 1000, 900),     # the rest of the same table
    "icon": PixelBox(940, 280, 990, 320),      # a small picture over the table's top edge
}
WHERE = SourceLocator(file_name="r.pdf", label="r.pdf", page=1)


def _node(node_id: str, node_type: NodeType) -> GraphNode:
    return GraphNode(id=node_id, document_id="d", type=node_type, label=node_id,
                     text=None if node_type is NodeType.IMAGE else node_id, locator=WHERE,
                     retrievable=False)


def _report() -> ExtractedDocument:
    nodes = [_node("heading", NodeType.TEXT_CHUNK), _node("summary", NodeType.TEXT_CHUNK),
             _node("grid", NodeType.TABLE), _node("band", NodeType.TEXT_CHUNK),
             _node("rows", NodeType.TABLE), _node("icon", NodeType.IMAGE)]
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r.pdf",
                     locator=WHERE, retrievable=False)
    return ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r.pdf", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r.pdf")),
        nodes=(root, *nodes),
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d", source_id="d", target_id=n.id,
                              type=EdgeType.CONTAINS) for n in nodes),
        evidence=(),
    )


class _Placer:
    def box(self, node: GraphNode) -> PixelBox | None:
        return BOXES.get(node.id)


class _Store:
    def put(self, payload: bytes, *, suffix: str = "") -> str:
        return "cut"


def _widen(*members: str) -> tuple[dict, list]:
    cuts: list = []

    def crop(page: int, box: tuple[int, int, int, int]) -> bytes:
        cuts.append(box)
        return b"png"

    result = widen_figures(
        _report(),
        figures=(HierarchyFigureGroup(figure_id="grid", member_ids=members),),
        placers={1: _Placer()},
        page_of={key: 1 for key in BOXES},
        crop=crop,
        asset_store=_Store(),
    )
    return {n.id: n.metadata.get("part_of_figure") for n in result.nodes}, cuts


def test_a_heading_named_with_a_table_stays_outside_it_and_so_does_what_lies_between() -> None:
    parts, cuts = _widen("heading", "rows", "icon")

    assert parts["heading"] is None
    assert parts["summary"] is None
    # The table's own pieces are still one table, with the band row between them.
    assert parts["rows"] == "grid" and parts["band"] == "grid"
    # A picture drawn over its edge is still drawn with it.
    assert parts["icon"] == "grid"
    # The crop reaches the icon, not the heading or the summary above it.
    assert cuts and all(box[1] >= 280 for box in cuts)

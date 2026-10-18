"""Where a node sits on its rendered page, asked the same way for every format.

A worksheet says where a thing is by naming cells, and the renderer records the column and
row edges so the answer is a lookup. A slide says it by giving the shape a rectangle of the
slide, which scales to the page directly. Nothing downstream of this cares which it was:
the merge, the crop and the scope all work on pixels.

Keeping the two behind one question is what lets one merge serve both. Two placements of
the same pictures, derived independently, once elected different owners for one merged
picture and the hierarchy pass showed the model a different picture from the one it named.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from graphme.application.reading_order import stated_box
from graphme.application.xlsx_crop_regions import PixelBox, SheetPixelGrid
from graphme.domain.enums import NodeType
from graphme.domain.models import GraphNode

HAIRLINE_PIXELS = 1.0
"""What a rule with no stated thickness occupies, so it can be placed and merged."""


class DrawingLike(Protocol):
    """A DrawingML object as the XLSX drawing parser reports it.

    Only the three fields a box is measured from. The parser's own record carries more,
    and a placer has no business knowing the rest of it.
    """

    @property
    def anchor_from(self) -> Any: ...

    @property
    def anchor_to(self) -> Any: ...

    @property
    def transform(self) -> dict[str, Any] | None: ...


@runtime_checkable
class UnitPlacer(Protocol):
    """Answers where one node sits on the page it was rendered onto."""

    def box(self, node: GraphNode) -> PixelBox | None:
        """The node's pixel box, or None when the page does not show it."""
        ...


class CellRangePlacer:
    """A worksheet node, placed through the edges the renderer recorded.

    A picture or chart is measured by its DrawingML extent rather than the cells it is anchored to:
    it does not fill them, and `editAs="oneCell"` leaves the `to` marker stale.
    """

    def __init__(
        self,
        grid: SheetPixelGrid,
        *,
        extents: Mapping[int, DrawingLike] | None = None,
    ) -> None:
        self._grid = grid
        self._extents: Mapping[int, DrawingLike] = extents or {}

    def box(self, node: GraphNode) -> PixelBox | None:
        if node.type in (NodeType.IMAGE, NodeType.CHART):
            extent = self._drawing_extent(node)
            if extent is not None:
                return extent
        if not node.locator.cell_range:
            return None
        return self._grid.cell_range(node.locator.cell_range)

    def _drawing_extent(self, node: GraphNode) -> PixelBox | None:
        index = node.locator.metadata.get("xml_index")
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        item = self._extents.get(index)
        if item is None:
            return None
        return self._grid.drawing(
            anchor_from=item.anchor_from,
            anchor_to=item.anchor_to,
            transform=item.transform,
        )


GLYPHS_INSIDE_FRAME = 0.5
"""How much of a shape's drawn words must lie in its own frame to be its words at all."""


class NormalizedBoxPlacer:
    """A slide node, placed by scaling the rectangle the shape already carries.

    Extraction records a shape's position as a fraction of the slide, so the page size is
    the only thing needed. There is no lookup and no estimate.
    """

    def __init__(
        self,
        *,
        width: int,
        height: int,
        glyphs: Mapping[str, PixelBox] | None = None,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("page size must be positive to place a unit on it")
        self._width = width
        self._height = height
        self._glyphs = glyphs or {}
        """Where the renderer reported each shape's words actually drew. A text box is
        sized for the words it could hold, not the words it holds, so its own rectangle
        reaches past them -- a title reached into a picture beside it and was taken."""

    def box(self, node: GraphNode) -> PixelBox | None:
        frame = self._frame(node)
        drawn = self._glyphs.get(str(node.metadata.get("shape_id")))
        # Only text is sized by its words. A table holds rows of pictures with no words in
        # them, and placed by its text it ended above them. And the renderer numbers a
        # layout's shapes in their own space, so words drawn somewhere else than the
        # shape's own frame belong to some other shape that shares its number.
        if (
            drawn is not None
            and node.type is NodeType.TEXT_CHUNK
            and (frame is None or frame.covered_fraction(drawn) >= GLYPHS_INSIDE_FRAME)
        ):
            return drawn
        return frame

    def _frame(self, node: GraphNode) -> PixelBox | None:
        # A slide states a shape's rectangle as a fraction of the slide; a PDF states a
        # block's rectangle in points and the extraction records the fraction beside it.
        bbox = stated_box(node.metadata) or node.locator.bbox
        if bbox is None or len(bbox) != 4:
            return None
        x0, y0, x1, y1 = bbox
        left, top = x0 * self._width, y0 * self._height
        right, bottom = x1 * self._width, y1 * self._height
        # A line is drawn by its stroke, so zero width or height is how a rule is stated.
        # Refusing to place it left tick marks buried in a diagram standing as units of
        # their own, because what is never placed is never merged into anything.
        flat_across = right <= left
        flat_down = bottom <= top
        if flat_across and flat_down:
            return None  # a point is not drawn; there is nothing on the page
        return PixelBox(
            x0=left,
            y0=top,
            x1=left + HAIRLINE_PIXELS if flat_across else right,
            y1=top + HAIRLINE_PIXELS if flat_down else bottom,
        )


class FlowPlacer:
    """A flow document's node, placed where the layout actually put it.

    A page of DOCX states no coordinates: the words fall where the line breaks put them,
    and the same paragraph moves when anything above it changes. The renderer reports
    which body element every run came from, so a paragraph is the union of its runs, a
    table the union of the runs in its cells, and a drawing its own measured bounds.
    """

    def __init__(
        self,
        boxes: Mapping[tuple[str, str, int], PixelBox],
        drawings: Mapping[tuple[str, str, int], Sequence[PixelBox]] | None = None,
    ) -> None:
        self._drawings = {
            index: list(items)
            for (story, _instance, index), items in (drawings or {}).items()
            if story == "body"
        }
        self._body: dict[int, PixelBox] = {}
        anchored: dict[int, dict[int, PixelBox]] = {}
        for (story, instance, index), box in boxes.items():
            if story == "body":
                self._body[index] = (
                    box if index not in self._body else self._body[index].union(box)
                )
                continue
            anchor, shape = _anchor_of(instance)
            if anchor is None:
                continue
            held = anchored.setdefault(anchor, {})
            held[shape] = box if shape not in held else held[shape].union(box)
        # A text box is named by which one it is on its anchoring paragraph, because that
        # is all the extraction can know about it: the shape numbers are the renderer's.
        self._boxes = {
            (anchor, order): box
            for anchor, held in anchored.items()
            for order, (_shape, box) in enumerate(sorted(held.items()), start=1)
        }
        # What the renderer drew hanging off each body element, words aside: a table's
        # cells hold pictures and text boxes that its runs alone do not reach.
        self._drawn_in: dict[int, list[PixelBox]] = {
            index: [*self._drawings.get(index, ()), *anchored.get(index, {}).values()]
            for index in {*self._drawings, *anchored}
        }

    def box(self, node: GraphNode) -> PixelBox | None:
        body_index = node.metadata.get("body_index")
        if not isinstance(body_index, int) or isinstance(body_index, bool):
            return None
        # The extraction counts body elements from one and the renderer from zero.
        index = body_index - 1
        if node.type is NodeType.IMAGE:
            # A picture hangs off a paragraph but is not part of its words.
            drawn = self._drawings.get(index, [])
            order = node.metadata.get("image_index")
            place = order - 1 if isinstance(order, int) and not isinstance(order, bool) else 0
            return drawn[place] if 0 <= place < len(drawn) else None
        if node.metadata.get("story") == "textbox":
            order = node.metadata.get("box_index")
            if not isinstance(order, int):
                return None
            return self._boxes.get((index, order))
        body = self._body.get(index)
        if node.type is NodeType.TABLE:
            # A row of photographs has no runs, so the words alone leave it outside the
            # table. What the renderer drew inside the table's cells is the table too.
            for box in self._drawn_in.get(index, ()):
                body = box if body is None else body.union(box)
        return body


def _anchor_of(instance: str) -> tuple[int | None, int]:
    """The body element a floating shape hangs from, and which shape it is on it.

    The renderer names a story instance "body:body:<anchor>.<shape>"; nothing else in it
    is ours to read.
    """
    tail = instance.rsplit(":", 1)[-1]
    anchor, _, shape = tail.partition(".")
    try:
        return int(anchor), int(shape or 0)
    except ValueError:
        return None, 0


def home_pages(
    nodes: Sequence[GraphNode], placers: Mapping[int, UnitPlacer]
) -> dict[str, int]:
    """The page each node belongs to: the one showing most of it.

    A paragraph broken over a page break draws on both, and so does a long table. Cropping
    it twice would leave the second crop standing for the whole, and deciding it twice
    would ask the same question of two pages and let the answers disagree.
    """
    home: dict[str, tuple[float, int]] = {}
    for index, placer in placers.items():
        for node in nodes:
            box = placer.box(node)
            if box is None:
                continue
            left, top, right, bottom = box.rounded()
            area = float((right - left) * (bottom - top))
            if node.id not in home or area > home[node.id][0]:
                home[node.id] = (area, index)
    return {node_id: index for node_id, (_area, index) in home.items()}

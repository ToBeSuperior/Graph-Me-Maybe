"""Place source-extracted units on a rendered sheet image, with no model in the loop.

Stage 3 already knows what every unit is and which cells it occupies: tables from
detected borders, pictures and charts from DrawingML anchors. The renderer records
where each column and row boundary landed, so those cell coordinates become pixel
coordinates by lookup rather than by estimation.

Units that overlap are one region. A picture overhanging its table drags the table's
rectangle out to cover it, the way adjoining territory merges; two comparison tables
sitting side by side stay separate, because a sheet routinely lays them out that way.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from openpyxl.utils import range_boundaries

EMU_PER_INCH = 914_400
PIXELS_PER_INCH = 96
TOUCH_SLACK_PIXELS = 2.0
ENCLOSURE_THRESHOLD = 0.9
"""How much of a connector has to lie inside a region before it is part of it. Boxes
overhang a little, so exact containment is too strict."""
TEXT_OVERLAP_THRESHOLD = 0.10
"""How far text has to run into a figure to be part of what the figure shows.

This is a judgement between measured cases. A caption running under a screenshot lay about
a third inside it and belongs to it. A drawing's labels -- a word or two written beside
the part they point at -- lie mostly outside the picture and reach a fifth or less
of the way in, and they are what the drawing is saying. Against those, a title beside a
decorative picture reached an eighth of the way in, and taking it made that picture claim
most of the slide. Nothing about the shapes themselves separates the two, so the line is
drawn between the numbers and kept low enough to hold a label; it can be moved again if a
document disagrees."""
"""Rendered borders round to whole pixels, so near-adjacency still counts as contact."""


class AnchorLike(Protocol):
    """A DrawingML anchor marker: a zero-based cell plus an offset into it, in EMU."""

    col: int | None
    col_off: int | None
    row: int | None
    row_off: int | None


@dataclass(frozen=True, slots=True)
class PixelBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def touches(self, other: PixelBox, *, slack: float = TOUCH_SLACK_PIXELS) -> bool:
        return not (
            self.x1 + slack < other.x0
            or other.x1 + slack < self.x0
            or self.y1 + slack < other.y0
            or other.y1 + slack < self.y0
        )

    def covered_fraction(self, other: PixelBox) -> float:
        """How much of `other` lies inside this box, as a fraction of `other`'s area."""
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        if width <= 0 or height <= 0:
            return 0.0
        area = (other.x1 - other.x0) * (other.y1 - other.y0)
        return 0.0 if area <= 0 else (width * height) / area

    def contains(self, other: PixelBox, *, slack: float = TOUCH_SLACK_PIXELS) -> bool:
        return (
            self.x0 <= other.x0 + slack
            and self.y0 <= other.y0 + slack
            and self.x1 + slack >= other.x1
            and self.y1 + slack >= other.y1
        )

    def union(self, other: PixelBox) -> PixelBox:
        return PixelBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def rounded(self) -> tuple[int, int, int, int]:
        return round(self.x0), round(self.y0), round(self.x1), round(self.y1)


@dataclass(frozen=True, slots=True)
class SheetPixelGrid:
    """Where each rendered column and row boundary landed in the sheet image."""

    column_edges: dict[int, int]
    row_edges: dict[int, int]
    scale: float
    device_pixel_ratio: float

    def emu(self, value: int | None) -> float:
        if not value:
            return 0.0
        return value / EMU_PER_INCH * PIXELS_PER_INCH * self.scale * self.device_pixel_ratio

    def cell_range(self, ref: str) -> PixelBox | None:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        if None in (min_col, min_row, max_col, max_row):
            return None
        try:
            return PixelBox(
                self.column_edges[min_col],
                self.row_edges[min_row],
                self.column_edges[max_col + 1],
                self.row_edges[max_row + 1],
            )
        except KeyError:
            return None  # the unit lies outside what was rendered

    def drawing(
        self,
        *,
        anchor_from: AnchorLike | None,
        anchor_to: AnchorLike | None = None,
        transform: dict[str, Any] | None = None,
    ) -> PixelBox | None:
        if anchor_from is None or anchor_from.col is None or anchor_from.row is None:
            return None
        try:
            x0 = self.column_edges[anchor_from.col + 1] + self.emu(anchor_from.col_off)
            y0 = self.row_edges[anchor_from.row + 1] + self.emu(anchor_from.row_off)
        except KeyError:
            return None
        # With editAs="oneCell" a drawing keeps its own size while cells resize around
        # it, so the stored extent is what was rendered and the `to` marker can be stale.
        extent = (transform or {}).get("ext") or {}
        if extent.get("cx") and extent.get("cy"):
            return PixelBox(x0, y0, x0 + self.emu(extent["cx"]), y0 + self.emu(extent["cy"]))
        if anchor_to is None or anchor_to.col is None or anchor_to.row is None:
            return None
        try:
            return PixelBox(
                x0,
                y0,
                self.column_edges[anchor_to.col + 1] + self.emu(anchor_to.col_off),
                self.row_edges[anchor_to.row + 1] + self.emu(anchor_to.row_off),
            )
        except KeyError:
            return None


MEET_SHARE = 0.5
"""How much of the narrower of two drawings their shared span has to cover, across or down,
for them to meet along a side rather than at a corner."""


def _meet(left: PixelBox, right: PixelBox) -> bool:
    across = min(left.x1, right.x1) - max(left.x0, right.x0)
    down = min(left.y1, right.y1) - max(left.y0, right.y0)
    narrower = min(left.x1 - left.x0, right.x1 - right.x0)
    shorter = min(left.y1 - left.y0, right.y1 - right.y0)
    return across >= MEET_SHARE * narrower or down >= MEET_SHARE * shorter


def _shares_area(left: PixelBox, right: PixelBox) -> bool:
    return (
        min(left.x1, right.x1) > max(left.x0, right.x0)
        and min(left.y1, right.y1) > max(left.y0, right.y0)
    )


def _region_kind_of(kinds: set[str]) -> str:
    """What a group of units counts as once they are one region.

    A region holding a table is a table's territory; one holding any drawing is a
    drawing's. Only a region of nothing but connectors is still a connector, and only one
    of nothing but text is still text.
    """
    if "table" in kinds:
        return "table"
    if "drawing" in kinds:
        return "drawing"
    return "text" if "text" in kinds else "connector"


def _belong_together(
    left: PixelBox,
    right: PixelBox,
    left_kind: str,
    right_kind: str,
    *,
    slack: float,
    left_parts: Sequence[PixelBox] = (),
    right_parts: Sequence[PixelBox] = (),
) -> bool:
    """Whether two regions are one thing on the page.

    `*_parts` are the things in a region that can hold text: each table as its own frame,
    and the region's drawings together as one picture -- strips of one screenshot enclose
    the labels between them, a table and a callout drawn over its edge enclose nothing
    beyond themselves. Text is judged against those, not against the rectangle the region
    grew to cover.
    """
    if not left.touches(right, slack=slack):
        return False
    pair = (left_kind, right_kind)
    nested = left.contains(right, slack=slack) or right.contains(left, slack=slack)
    # A connector joins by saying so. Its rectangle spans the diagonal between the shapes
    # it links and crosses whatever lies between, so it never takes anything in by
    # position. Being taken in is the other direction: an arrow drawn within a diagram is
    # part of that diagram like any other piece.
    if "connector" in pair:
        if pair == ("connector", "connector"):
            return False
        arrow, host = (left, right) if left_kind == "connector" else (right, left)
        # Text owns nothing, so a line drawn inside a labelled box stays a line.
        if "text" in pair:
            return False
        # Like text, a line is inside a table or the picture, not inside the rectangle a
        # region grew to: a divider across a slide lay in that rectangle and in nothing else.
        holders = (right_parts if left_kind == "connector" else left_parts) or (host,)
        return any(part.covered_fraction(arrow) >= ENCLOSURE_THRESHOLD for part in holders)
    # Text belongs to something only by being inside it. A shape's box routinely brushes
    # its neighbour's without the two being one thing: a title grazing a decorative
    # picture by an eighth of itself was taken by that picture, which then grew across the
    # slide. A drawing is different -- a photograph hanging over the edge of the table it
    # sits in is still part of that table.
    if "text" in pair:
        if pair == ("text", "text"):
            return False
        # A region's rectangle is the union of what it is made of, and it grows over ground
        # none of them covers: a callout over a table's top edge reaches up to the slide's
        # title. Text is inside something only when it is inside a table or the picture.
        words, hosts = (left, right_parts) if left_kind == "text" else (right, left_parts)
        held = max(
            (max(host.covered_fraction(words), words.covered_fraction(host)) for host in hosts),
            default=0.0,
        )
        return held >= TEXT_OVERLAP_THRESHOLD
    if pair == ("table", "table"):
        return nested  # two tables side by side are a comparison, not one table
    if pair == ("drawing", "drawing"):
        # Strips of one picture abut along a side and are one picture; two pictures that
        # graze at a corner are two, and one rectangle around both covered whatever lay
        # between -- a table and the headings beside it.
        return _meet(left, right)
    # Anything else has to actually share area. Merely stopping where the next thing
    # begins is how a page is laid out, and merging on that let a table below a diagram
    # swallow the diagram.
    return nested or _shares_area(left, right)


def merge_regions(
    boxes: list[PixelBox] | tuple[PixelBox, ...],
    kinds: tuple[str, ...],
    *,
    slack: float = TOUCH_SLACK_PIXELS,
    joined: tuple[tuple[int, int], ...] = (),
) -> tuple[tuple[int, ...], ...]:
    """Group region indexes that form one territory, returned in input order.

    `joined` are pairs the document itself states belong together -- the two shapes an
    arrow connects. They are grouped whatever their boxes do, because a connector joins
    things that need not touch.
    """
    if len(boxes) != len(kinds):
        raise ValueError("every region needs a kind")
    parent = list(range(len(boxes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in joined:
        # A join says two shapes read together, not that text lies inside a picture: text
        # comes into a region only by lying in it.
        if (
            0 <= left < len(boxes)
            and 0 <= right < len(boxes)
            and "text" not in (kinds[left], kinds[right])
        ):
            parent[find(left)] = find(right)

    # Territory grows, and what a region then covers is inside it. Comparing only the
    # boxes a unit started with left labels standing beside a screenshot that had grown
    # around them, so the pass repeats over the claimed boxes until nothing moves.
    while True:
        claimed: dict[int, PixelBox] = {}
        held: dict[int, set[str]] = {}
        tables: dict[int, list[PixelBox]] = {}
        picture: dict[int, PixelBox] = {}
        for index in range(len(boxes)):
            root = find(index)
            claimed[root] = (
                boxes[index] if root not in claimed else claimed[root].union(boxes[index])
            )
            held.setdefault(root, set()).add(kinds[index])
            if kinds[index] == "table":
                tables.setdefault(root, []).append(boxes[index])
            elif kinds[index] == "drawing":
                picture[root] = (
                    boxes[index] if root not in picture else picture[root].union(boxes[index])
                )
        parts = {
            root: [*tables.get(root, ()), *([picture[root]] if root in picture else [])]
            for root in claimed
        }
        roots = sorted(claimed)
        merged = False
        for position, left_root in enumerate(roots):
            # A root that was merged away earlier in this pass is not compared again:
            # its box now belongs to another territory while `held` still describes the
            # old one, and judging that pair would mix two different things. The next
            # pass sees the union and the kinds together.
            if find(left_root) != left_root:
                continue
            for right_root in roots[position + 1 :]:
                if find(right_root) != right_root:
                    continue
                if _belong_together(
                    claimed[left_root],
                    claimed[right_root],
                    _region_kind_of(held[left_root]),
                    _region_kind_of(held[right_root]),
                    slack=slack,
                    left_parts=parts.get(left_root, ()),
                    right_parts=parts.get(right_root, ()),
                ):
                    parent[left_root] = right_root
                    merged = True
                    break
        if not merged:
            break

    groups: dict[int, list[int]] = {}
    for index in range(len(boxes)):
        groups.setdefault(find(index), []).append(index)
    return tuple(tuple(group) for group in groups.values())

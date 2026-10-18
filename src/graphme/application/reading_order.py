"""The order a reader takes down a page, which on a two-column page is not the order of tops.

Read top to bottom, a two-column page zigzags: the head of the right column comes before
the foot of the left, and a section that starts at the top of the right column arrives
before the heading it belongs under at the foot of the left. So the page is cut into bands
at whatever crosses it, and inside a band each column is read to its foot before the next.

Both passes that read a page in order -- the one joining printed pieces into units and the
one placing units under one another -- read it this one way. They used to sort by top edge
each on their own, and the outline pass kept doing so after the joining pass was fixed:
`3.2` reached the model before the `3.` it belongs under.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
Box = Sequence[float]
"""left, top, right, bottom -- in whatever units `width` is given in."""

SPANS_THE_PAGE = 0.6
"""How wide a thing has to be, as a share of the page, to belong to no column. A title, a
wide figure and its caption cross the page; everything narrower sits in one column."""

SAME_COLUMN_OVERLAP = 0.5
"""How much of the narrower of two things has to lie over the other for them to be in one
column. A column's blocks are not all the same width -- a paragraph's last line is short,
an indented one starts further in -- so what makes a column is that they stand over each
other, not that they measure the same."""


def reading_order(
    items: Sequence[T], box: Callable[[T], Box | None], *, width: float
) -> list[T]:
    """One page's items in the order they are read.

    An item with no box -- a format that draws nothing, a document or a sheet -- has no
    column to be read in, so it keeps the place it was given.
    """
    return [item for item, _ in in_columns(items, box, width=width)]


def in_columns(
    items: Sequence[T], box: Callable[[T], Box | None], *, width: float
) -> list[tuple[T, float]]:
    """Each item in reading order, with the left edge of the column it is read in.

    Where a line starts only says how far it is set in when it is measured from its own
    column. Measured from the page, every line of the right column starts further in than
    every line of the left, and reads as nested under it.
    Whatever crosses the page is its own column, starting at the page's left margin.
    """
    def top_left(item: T) -> tuple[float, float]:
        found = box(item)
        return (float(found[1]), float(found[0])) if found is not None else (0.0, 0.0)

    lefts = [float(found[0]) for item in items if (found := box(item)) is not None]
    margin = min(lefts, default=0.0)
    band: list[T] = []
    ordered: list[tuple[T, float]] = []
    for item in sorted(items, key=top_left):
        found = box(item)
        if found is not None and found[2] - found[0] >= SPANS_THE_PAGE * width:
            # Whatever crosses the page ends the band above it and stands on its own.
            ordered.extend(_by_column(band, box, top_left))
            ordered.append((item, margin))
            band = []
            continue
        band.append(item)
    ordered.extend(_by_column(band, box, top_left))
    return ordered


def _by_column(
    band: Sequence[T],
    box: Callable[[T], Box | None],
    top_left: Callable[[T], tuple[float, float]],
) -> list[tuple[T, float]]:
    """Each column of a band, read to its foot, left to right."""
    columns: list[tuple[list[float], list[T]]] = []
    for item in sorted(band, key=lambda each: top_left(each)[1]):
        found = box(item)
        for span, members in columns:
            if found is None or overlap(span, found) >= SAME_COLUMN_OVERLAP:
                if found is not None:
                    span[0], span[1] = min(span[0], found[0]), max(span[1], found[2])
                members.append(item)
                break
        else:
            # A boxless item starts an empty span, which no drawn box overlaps, so the
            # items that have nowhere to be stay together in the order they came.
            span = [float(found[0]), float(found[2])] if found is not None else [0.0, 0.0]
            columns.append((span, [item]))
    ordered: list[tuple[T, float]] = []
    for span, members in sorted(columns, key=lambda column: column[0][0]):
        ordered.extend((item, span[0]) for item in sorted(members, key=top_left))
    return ordered


def overlap(span: Sequence[float], box: Box) -> float:
    """How much of the narrower of the two lies over the other."""
    narrower = min(span[1] - span[0], box[2] - box[0])
    if narrower <= 0:
        return 0.0
    return max(0.0, min(span[1], box[2]) - max(span[0], box[0])) / narrower

"""A PDF table the file states in pieces, put back together.

The file states each page apart, and the table finder stops at a band of words with no rule
around it, so one table can arrive as two: broken by the page end (`continued_tables`), or
by a band across it (`band_between`). Both are told by the grid of columns they are drawn on.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol, cast

from graphme.domain.enums import NodeType
from graphme.domain.models import JsonValue


class Piece(Protocol):
    """What a table piece is judged by: where it is, what it says and the grid it is on."""

    @property
    def page(self) -> int: ...

    @property
    def occurrence(self) -> int: ...

    @property
    def type(self) -> NodeType: ...

    @property
    def text(self) -> str | None: ...

    @property
    def bbox(self) -> tuple[float, float, float, float]: ...

    @property
    def metadata(self) -> dict[str, JsonValue]: ...


GRID_SLACK = 3.0
"""Points two drawings of one grid may differ by: the same column edge drawn again, or a
band's edge against the frame it is printed in."""
RECUR_SLACK = 3.0
"""Points a running header or a page number may move between two pages and still be the
same one: a page's furniture is set at one height on every page."""


def continued_tables(
    pages: Sequence[Sequence[Piece]],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Each table that carries on one broken off by the page before: (page, occurrence) of
    the continuation to that of the table it continues.

    The page end breaks a table where the page runs out, so the piece before is the last
    thing on its page and the piece after the first on the next -- with nothing between but
    what every page carries, a running header or a page number -- and both are drawn on
    the same columns. A heading or a line of text between them starts another table.
    """
    held: dict[tuple[int, int], tuple[int, int]] = {}
    for before, after in zip(pages, pages[1:], strict=False):
        tables_before = [u for u in before if u.type is NodeType.TABLE]
        tables_after = [u for u in after if u.type is NodeType.TABLE]
        if not tables_before or not tables_after:
            continue
        last = max(tables_before, key=lambda u: u.bbox[3])
        first = min(tables_after, key=lambda u: u.bbox[1])
        below = [u for u in before if u is not last and u.bbox[1] >= last.bbox[3]]
        above = [u for u in after if u is not first and u.bbox[3] <= first.bbox[1]]
        if (_same_grid(last, first) and all(_recurs(u, after) for u in below)
                and all(_recurs(u, before) for u in above)):
            # A table running over three pages hangs every piece from the first.
            root = held.get((last.page, last.occurrence), (last.page, last.occurrence))
            held[(first.page, first.occurrence)] = root
    return held


def band_between(
    upper: Piece, lower: Piece, units: Sequence[Piece]
) -> Piece | None:
    """The line printed across one table that split it in two, or None.

    A band with no rule around it -- "Plant 2" over the rows that follow -- stops the table
    finder: two tables, the band a line between them. It is one when both halves are drawn
    on the same columns and the only thing between them is one line inside their frame,
    taking no more room than a row of theirs. A heading between two tables stands apart."""
    if not _same_grid(upper, lower) or lower.bbox[1] < upper.bbox[3]:
        return None
    rows = int(cast(int, upper.metadata.get("row_count") or 1))
    row = (upper.bbox[3] - upper.bbox[1]) / max(1, rows)
    if lower.bbox[1] - upper.bbox[3] > row + GRID_SLACK:
        return None
    between = [unit for unit in units
               if unit is not upper and unit is not lower
               and unit.bbox[1] >= upper.bbox[3] - GRID_SLACK
               and unit.bbox[3] <= lower.bbox[1] + GRID_SLACK]
    if len(between) != 1:
        return None
    band = between[0]
    inside = (band.bbox[0] >= upper.bbox[0] - GRID_SLACK
              and band.bbox[2] <= upper.bbox[2] + GRID_SLACK)
    one_line = band.type is NodeType.TEXT_CHUNK and "\n" not in (band.text or "\n")
    return band if inside and one_line else None


def _same_grid(one: Piece, other: Piece) -> bool:
    edges = [cast(list[float], unit.metadata.get("column_edges") or []) for unit in (one, other)]
    return bool(edges[0]) and len(edges[0]) == len(edges[1]) and all(
        abs(a - b) <= GRID_SLACK for a, b in zip(edges[0], edges[1], strict=True))


def _recurs(unit: Piece, page: Sequence[Piece]) -> bool:
    """The same thing at the same height on the other page -- its numbers aside, as a page
    number's are."""
    def said(u: Piece) -> str:
        return re.sub(r"\d+", "#", " ".join((u.text or "").split()))
    return any(other.type is unit.type and said(other) == said(unit)
               and abs(other.bbox[1] - unit.bbox[1]) <= RECUR_SLACK for other in page)

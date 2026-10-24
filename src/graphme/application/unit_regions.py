"""Merge what overlaps on a sheet into the regions that get cropped.

Units arrive already placed: a table at the pixels its cell range covers, a picture at
the pixels its DrawingML extent covers. Whatever overlaps is one thing to cut out. A
photograph spilling past a results table pulls the table's rectangle out to cover it,
and a photograph wholly inside one stops being a unit of its own — the table is what
you would hand someone, not fifty slivers of it.

Two tables side by side stay apart even when their borders touch, because a sheet
routinely lays comparison tables out that way; a table that encloses another is the
one exception, and swallows it.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from graphme.application.page_regions import (
    PixelBox,
    merge_regions,
)

RegionKind = Literal["table", "drawing", "text", "connector"]
"""A table names a region and swallows what it encloses; drawings merge on touch;
text merges only into something that encloses it, so two paragraphs side by side
stay two units; a connector merges only with the shapes it states it joins, because its
own rectangle spans a diagonal and crosses whatever happens to lie between them."""


HOLDER_COVERAGE = 0.9
"""How much of a region one member has to fill to be the thing the region is called."""


@dataclass(frozen=True, slots=True)
class PlacedUnit:
    """One unit already measured on the rendering."""

    id: str
    kind: RegionKind
    box: PixelBox
    joins: tuple[str, ...] = ()
    """The units a connector states it joins. An arrow's own rectangle spans the diagonal
    between the two shapes and brushes whatever lies in that span, so position cannot say
    what it connects -- and the two it joins may not touch each other at all."""


@dataclass(frozen=True, slots=True)
class UnitRegion:
    kind: RegionKind
    box: PixelBox
    lead_id: str
    """The unit that stands for the region: its table, or the top-left picture."""
    member_ids: tuple[str, ...]
    drawing_count: int


def build_unit_regions(units: Sequence[PlacedUnit]) -> tuple[UnitRegion, ...]:
    """Return one region per croppable unit, in reading order (top-left first)."""
    if not units:
        return ()
    boxes = [unit.box for unit in units]
    kinds = tuple(unit.kind for unit in units)
    index_of = {unit.id: index for index, unit in enumerate(units)}
    stated = tuple(
        (index_of[unit.id], index_of[other])
        for unit in units
        for other in unit.joins
        if other in index_of
    )

    regions: list[UnitRegion] = []
    for group in merge_regions(boxes, kinds, joined=stated):
        members = [units[index] for index in group]
        claimed = members[0].box
        for member in members[1:]:
            claimed = claimed.union(member.box)
        # Whatever holds the rest names the region: a diagram with a small table drawn
        # inside it is a diagram, and the largest such holder is the one holding the
        # others. Requiring one to contain every other exactly was too brittle -- a single
        # arrow poking a few pixels past a diagram's edge left no holder at all.
        tables = [member for member in members if member.kind == "table"]
        # An arrow is never what a diagram is called, and text is never what a region
        # holding anything else is called: enclosed text is part of what encloses it, and a
        # panel of words drawn large enough to cover a table must not end up owning it.
        never_named = {"connector"}
        if any(member.kind != "text" for member in members):
            never_named.add("text")
        holders = [
            member
            for member in members
            if member.kind not in never_named
            and member.box.covered_fraction(claimed) >= HOLDER_COVERAGE
        ]
        if holders:
            lead = max(holders, key=lambda unit: unit.box.area)
        else:
            # Failing that a table names it, then the top-left drawing; text names one
            # only when it is alone, since enclosed text is part of what encloses it.
            drawings = [member for member in members if member.kind == "drawing"]
            named = [member for member in members if member.kind != "connector"]
            lead = min(
                tables or drawings or named or members,
                key=lambda unit: (unit.box.y0, unit.box.x0),
            )
        regions.append(
            UnitRegion(
                kind="table" if tables else "drawing",
                box=claimed,
                lead_id=lead.id,
                member_ids=tuple(member.id for member in members),
                drawing_count=sum(1 for member in members if member.kind == "drawing"),
            )
        )
    return tuple(sorted(regions, key=lambda region: (region.box.y0, region.box.x0)))

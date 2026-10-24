"""A table's own photographs do not make a picture that reaches out of the table.

A results table held a row of photographs, and a small icon sat over the table's top edge.
The region's drawings were taken together as one picture -- one rectangle from the icon down
to the photographs -- and the section heading and its summary line, printed above the table,
lay inside that rectangle and became parts of the table.
"""
from __future__ import annotations

from graphme.application.page_regions import PixelBox
from graphme.application.unit_regions import PlacedUnit, build_unit_regions


def _box(x0: float, y0: float, x1: float, y1: float) -> PixelBox:
    return PixelBox(x0=x0, y0=y0, x1=x1, y1=y1)


def test_a_heading_above_a_table_of_photographs_stays_out_of_the_table() -> None:
    units = (
        PlacedUnit("heading", "text", _box(64, 556, 683, 593)),
        PlacedUnit("summary", "text", _box(81, 606, 1051, 637)),
        PlacedUnit("icon", "drawing", _box(1480, 574, 1594, 670)),
        PlacedUnit("table", "table", _box(100, 653, 1588, 1047)),
        PlacedUnit("photo-a", "drawing", _box(181, 775, 405, 925)),
        PlacedUnit("photo-b", "drawing", _box(1127, 775, 1351, 925)),
    )

    regions = build_unit_regions(units)

    table = next(region for region in regions if "table" in region.member_ids)
    assert table.lead_id == "table"
    assert {"photo-a", "photo-b"} <= set(table.member_ids)
    assert not {"heading", "summary"} & set(table.member_ids)

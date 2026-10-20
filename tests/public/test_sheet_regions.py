"""Two drawings that only graze at a corner are two pictures. Invented boxes.

A chart beside a table and a photograph below it grazed at one corner; merged, their rectangle
covered the table and the headings between them, and the table came to own the chart, the
photograph and both headings -- the sheet's outline read every section under the table."""
from __future__ import annotations

from graphme.application.xlsx_crop_regions import PixelBox, merge_regions

TABLE = PixelBox(130, 200, 530, 480)
CHART = PixelBox(670, 150, 1670, 680)  # right of the table, reaching just below it
PHOTO = PixelBox(130, 640, 770, 1040)  # below the table, its top right under the chart's corner
HEADING = PixelBox(130, 600, 270, 640)  # "2. Defect photo", just above the photograph


def test_a_chart_and_a_photo_grazing_at_a_corner_stay_apart_and_take_nothing() -> None:
    groups = merge_regions([TABLE, CHART, PHOTO, HEADING], ("table", "drawing", "drawing", "text"))
    assert sorted(groups) == [(0,), (1,), (2,), (3,)]


def test_strips_of_one_picture_meeting_along_a_side_are_one() -> None:
    upper, lower = PixelBox(100, 100, 600, 300), PixelBox(100, 300, 600, 500)
    label = PixelBox(250, 280, 450, 320)  # written across the seam
    groups = merge_regions([upper, lower, label], ("drawing", "drawing", "text"))
    assert groups == ((0, 1, 2),)

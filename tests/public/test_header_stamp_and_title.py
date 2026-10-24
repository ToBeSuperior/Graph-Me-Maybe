"""A page title and a stamp printed far apart in the header are two units, not one.

pymupdf gathered a slide's title at the left and a small "confidential" stamp at the far right
into one block. As one unit neither could be decided on its own -- the stamp could not be
dropped without the title -- and the block, spanning the whole header, covered the icons
drawn there and was taken in by them, so the title never reached the outline.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.application.page_regions import PixelBox
from graphme.application.unit_regions import PlacedUnit, build_unit_regions
from graphme.domain.enums import NodeType


def _slide_with_a_stamp(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=828, height=540)
    page.insert_text((790, 14), "대외비", fontsize=10.6, fontname="korea")
    page.insert_text((14, 40), "Ⅱ. 시험 결과", fontsize=28, fontname="korea")
    page.insert_text((40, 120), "시료 다섯 개 모두 기준을 만족했다.", fontsize=14,
                     fontname="korea")
    document.save(path)
    return path


def test_a_title_and_a_stamp_far_apart_in_one_block_are_two_units(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(_slide_with_a_stamp(tmp_path / "slide.pdf"))

    texts = sorted(n.text or "" for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK)
    assert "대외비" in texts
    assert "Ⅱ. 시험 결과" in texts


def _box(x0: float, y0: float, x1: float, y1: float) -> PixelBox:
    return PixelBox(x0=x0, y0=y0, x1=x1, y1=y1)


def test_a_line_running_across_a_row_of_icons_is_not_taken_in_by_them() -> None:
    units = (
        PlacedUnit("header", "text", _box(26, 8, 1640, 90)),
        PlacedUnit("icon-1", "drawing", _box(1112, 11, 1242, 98)),
        PlacedUnit("icon-2", "drawing", _box(1240, 11, 1370, 98)),
        PlacedUnit("icon-3", "drawing", _box(1368, 11, 1498, 98)),
    )

    regions = build_unit_regions(units)

    icons = next(region for region in regions if "icon-1" in region.member_ids)
    assert "header" not in icons.member_ids

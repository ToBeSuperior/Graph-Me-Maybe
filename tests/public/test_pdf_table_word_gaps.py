"""A table's cells read their words the way the page's text does.

Some PDFs move the pen between words without writing a space. The page's text puts a space in
that gap (`page_text`), but a table read through the table finder's own extraction did not,
and a Korean note in a table cell came out as one run of letters.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.domain.enums import NodeType

SIZE = 14


def _report_with_a_table(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    left, middle, right = 72, 220, 480
    rows = (120, 170, 220)
    for y in rows:
        page.draw_line((left, y), (right, y))
    for x in (left, middle, right):
        page.draw_line((x, rows[0]), (x, rows[-1]))
    page.insert_text((left + 8, 150), "항목", fontsize=SIZE, fontname="korea")
    page.insert_text((middle + 8, 150), "판정", fontsize=SIZE, fontname="korea")
    page.insert_text((left + 8, 200), "외관", fontsize=SIZE, fontname="korea")
    # Two words drawn apart, with no space written between them.
    width = pymupdf.Font("korea").text_length("표면", fontsize=SIZE)
    page.insert_text((middle + 8, 200), "표면", fontsize=SIZE, fontname="korea")
    page.insert_text((middle + 8 + width + 0.2 * SIZE, 200), "양호", fontsize=SIZE,
                     fontname="korea")
    document.save(path)
    return path


def test_words_drawn_apart_in_a_table_cell_keep_their_space(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(_report_with_a_table(tmp_path / "report.pdf"))

    tables = [n for n in extracted.nodes if n.type is NodeType.TABLE]
    assert len(tables) == 1
    assert "표면 양호" in (tables[0].text or "")

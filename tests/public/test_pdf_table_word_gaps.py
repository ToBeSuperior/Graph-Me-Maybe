"""A table's cells read their words the way the page's text does.

Some PDFs move the pen between words without writing a space. The page's text puts a space in
that gap (`page_text`), but a table read through the table finder's own extraction did not,
and a Korean note in a table cell came out as one run of letters.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_a_tables_text_is_read_once_not_once_a_cell(tmp_path: Path, monkeypatch: Any) -> None:
    """Every page read builds the page's whole text: one a cell made a long table's page cost
    seconds, all of it holding the interpreter lock."""
    reads: list[object] = []
    read = pymupdf.Page.get_text

    def counted(page: pymupdf.Page, *args: Any, **options: Any) -> Any:
        if options.get("clip") is not None:
            reads.append(options["clip"])
        return read(page, *args, **options)

    monkeypatch.setattr(pymupdf.Page, "get_text", counted)
    extracted = PDFExtractor().extract(_report_with_a_table(tmp_path / "report.pdf"))

    table = next(n for n in extracted.nodes if n.type is NodeType.TABLE)
    assert all(word in (table.text or "") for word in ("항목", "판정", "외관", "표면 양호"))
    inside = [r for r in map(pymupdf.Rect, reads) if r in pymupdf.Rect(72, 120, 480, 220)]
    assert len(inside) == 1  # the table's own, where every cell once took a read of its own

"""A table with a band of words across it is one table, the band one of its rows.

A table groups its rows under a line printed across it -- "Plant 2" -- with no rule drawn
around it. The table finder stops at the band: two tables, and the band a line of text
between them, which the outline then read as a heading with the second half under it. The
band takes one row's place in the table's grid; a heading between two tables stands apart.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.domain.enums import NodeType

COLUMNS = (60, 200, 340, 480)


def _grid(page: pymupdf.Page, top: float, first: int,
          columns: tuple[float, ...] = COLUMNS) -> None:
    for row in range(3):
        for column in range(len(columns) - 1):
            cell = pymupdf.Rect(columns[column], top + row * 20, columns[column + 1],
                                top + (row + 1) * 20)
            page.draw_rect(cell, width=0.8)
            page.insert_text((cell.x0 + 4, cell.y1 - 6), f"P-{first + row} c{column}",
                             fontsize=9)


def _results(path: Path, *, between: str, gap: float = 20,
             columns: tuple[float, ...] = COLUMNS) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 100), "4. Results", fontsize=14)
    _grid(page, 200, 100)
    page.insert_text((64, 260 + gap / 2 + 4), between, fontsize=9)
    _grid(page, 260 + gap, 200, columns)
    document.save(path)
    return path


def _units(path: Path) -> list[tuple[NodeType, str]]:
    extracted = PDFExtractor().extract(path)
    return [(n.type, n.text or "") for n in extracted.nodes if n.type is not NodeType.DOCUMENT]


def test_a_band_across_a_table_is_one_of_its_rows(tmp_path: Path) -> None:
    units = _units(_results(tmp_path / "r.pdf", between="Plant 2"))

    tables = [text for kind, text in units if kind is NodeType.TABLE]
    assert len(tables) == 1
    rows = tables[0].splitlines()
    assert rows[2].startswith("P-102") and rows[3] == "Plant 2" and rows[4].startswith("P-200")
    assert "Plant 2" not in [text for kind, text in units if kind is NodeType.TEXT_CHUNK]


def test_a_heading_standing_apart_between_two_tables_starts_another(tmp_path: Path) -> None:
    units = _units(_results(tmp_path / "r.pdf", between="Table 5. Seal replacements",
                            gap=60))

    assert [kind for kind, _ in units].count(NodeType.TABLE) == 2
    assert (NodeType.TEXT_CHUNK, "Table 5. Seal replacements") in units


def test_tables_on_different_grids_with_a_line_between_are_two(tmp_path: Path) -> None:
    units = _units(_results(tmp_path / "r.pdf", between="Plant 2",
                            columns=(60, 150, 340, 480)))

    assert [kind for kind, _ in units].count(NodeType.TABLE) == 2

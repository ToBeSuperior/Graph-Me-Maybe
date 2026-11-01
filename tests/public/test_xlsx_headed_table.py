"""A table on a sheet drawn with no border is still a table: a row of headings over rows of
values, at least one column of them all one kind of value. Invented data.

Only a border made a short table: without one each cell was a unit of its own and the outline
hung every value under the first heading, flat, its rows and columns lost. What tells a table
from loose cells is in any workbook, not in the ones at hand: headings in every column, rows
under them filling those columns, and a column holding values -- numbers, dates -- rather than
words. A list of words under one heading, or headings over a single row, is not one.
"""
from __future__ import annotations

from pathlib import Path

import xlsxwriter

from graphme.adapters.extractors.excel import ExcelExtractor
from graphme.domain.enums import NodeType


def _workbook(path: Path, rows: list[list[object]], *, at: str = "B3") -> Path:
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet("Inspection")
    sheet.write("A1", "Pump inspection, March")
    column, row = ord(at[0]) - ord("A"), int(at[1:]) - 1
    for offset, values in enumerate(rows):
        sheet.write_row(row + offset, column, values)
    sheet.write(row + len(rows) + 3, 0, "Checked by the night shift")
    book.close()
    return path


def _tables(path: Path) -> list[str]:
    extracted = ExcelExtractor().extract(path)
    return [n.locator.cell_range or "" for n in extracted.nodes if n.type is NodeType.TABLE]


def test_headings_over_rows_of_values_are_a_table(tmp_path: Path) -> None:
    rows: list[list[object]] = [["Pump", "Hours", "Result"], ["P-101", 2000, "pass"],
                                ["P-102", 2010, "fail"], ["P-103", 2020, "pass"]]

    assert _tables(_workbook(tmp_path / "w.xlsx", rows)) == ["B3:D6"]


def test_a_value_left_blank_in_a_row_keeps_the_table(tmp_path: Path) -> None:
    rows: list[list[object]] = [["Pump", "Hours", "Note"], ["P-101", 2000, ""],
                                ["P-102", 2010, "seal worn"], ["P-103", 2020, ""]]

    assert _tables(_workbook(tmp_path / "w.xlsx", rows)) == ["B3:D6"]


def test_rows_of_words_with_no_column_of_values_are_not_a_table(tmp_path: Path) -> None:
    rows: list[list[object]] = [["Step", "What to do"], ["Open", "Close the inlet valve"],
                                ["Drain", "Empty the casing"], ["Inspect", "Look at the seal"]]

    assert _tables(_workbook(tmp_path / "w.xlsx", rows)) == []


def test_headings_over_a_single_row_are_a_record_not_a_table(tmp_path: Path) -> None:
    rows: list[list[object]] = [["Pump", "Hours", "Result"], ["P-101", 2000, "pass"]]

    assert _tables(_workbook(tmp_path / "w.xlsx", rows)) == []

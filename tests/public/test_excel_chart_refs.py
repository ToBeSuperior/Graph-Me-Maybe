"""A chart whose series names a single cell, not a range -- one point plotted, or a title taken
from one cell -- is read like any other. Invented workbook."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

from graphme.adapters.extractors.excel import ExcelExtractor
from graphme.domain.enums import NodeType


def test_a_chart_series_of_one_cell_is_read(tmp_path: Path) -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "Scores"
    for row in (("Model", "Score"), ("Invented A", 0.71), ("Invented B", 0.64)):
        sheet.append(row)
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, min_row=2, max_row=2))  # one cell: Scores!$B$2
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=2))
    sheet.add_chart(chart, "D2")
    book.save(tmp_path / "scores.xlsx")

    extracted = ExcelExtractor().extract(tmp_path / "scores.xlsx")

    charts = [n for n in extracted.nodes if n.type == NodeType.CHART]
    assert len(charts) == 1
    assert "0.71" in str(charts[0].model_dump())


def test_a_chart_over_dates_is_read_with_its_dates_as_text(tmp_path: Path) -> None:
    # A chart by day -- dates along the axis -- made the whole workbook unreadable: a date is not
    # a value a node's metadata may hold.
    from datetime import date

    book = Workbook()
    sheet = book.active
    sheet.title = "Daily"
    sheet.append(("Day", "Pass", "Fail"))
    for day, ok, bad in ((date(2026, 10, 14), 29, 1), (date(2026, 10, 15), 30, 0)):
        sheet.append((day, ok, bad))
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=2, max_col=3, min_row=1, max_row=3),
                   titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=3))
    sheet.add_chart(chart, "E2")
    book.save(tmp_path / "daily.xlsx")

    extracted = ExcelExtractor().extract(tmp_path / "daily.xlsx")

    chart_node = next(n for n in extracted.nodes if n.type == NodeType.CHART)
    series = chart_node.metadata["series"]
    assert [s["title"] for s in series] == ["Pass", "Fail"]
    assert series[0]["categories"][0].startswith("2026-10-14")

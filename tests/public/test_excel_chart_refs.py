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

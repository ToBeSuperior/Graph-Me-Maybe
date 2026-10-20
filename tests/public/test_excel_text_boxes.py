"""A text box on a worksheet is text the file holds: it is read like any other. Invented data.

Only a box over a picture was kept, and only inside the picture's metadata; one over a chart or
on its own was dropped, and a caption written from the picture misread what it said."""
from __future__ import annotations

from pathlib import Path

import xlsxwriter

from graphme.adapters.extractors.excel import ExcelExtractor
from graphme.domain.enums import EdgeType, NodeType


def _workbook(path: Path) -> Path:
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet("Monthly")
    sheet.write_row("A1", ["Month", "Defects"])
    for row, (month, count) in enumerate([("Jan", 2), ("Feb", 3), ("Mar", 7)], start=2):
        sheet.write_row(f"A{row}", [month, count])
    chart = book.add_chart({"type": "column"})
    chart.add_series({"categories": "=Monthly!$A$2:$A$4", "values": "=Monthly!$B$2:$B$4"})
    sheet.insert_chart("D2", chart)
    sheet.insert_textbox("E4", "Defects jumped after the March swap", {"width": 200, "height": 40})
    sheet.insert_textbox("A20", "Checked by the night shift", {"width": 200, "height": 40})
    book.close()
    return path


def test_every_text_box_is_text_on_its_sheet(tmp_path: Path) -> None:
    extracted = ExcelExtractor().extract(_workbook(tmp_path / "dashboard.xlsx"))

    texts = {node.text: node for node in extracted.nodes if node.type is NodeType.TEXT_CHUNK}
    boxes = [texts.get(text) for text in
             ("Defects jumped after the March swap", "Checked by the night shift")]
    assert all(boxes), sorted(t or "" for t in texts)
    sheet = next(node for node in extracted.nodes if node.type is NodeType.WORKSHEET)
    held = {e.target_id for e in extracted.edges
            if e.type is EdgeType.CONTAINS and e.source_id == sheet.id}
    assert all(box.id in held and box.retrievable for box in boxes if box)

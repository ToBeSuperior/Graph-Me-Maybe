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


_USER_SHAPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<c:userShapes xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:cdr="http://schemas.openxmlformats.org/drawingml/2006/chartDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    '<cdr:relSizeAnchor><cdr:from><cdr:x>0.5</cdr:x><cdr:y>0.1</cdr:y></cdr:from>'
    '<cdr:to><cdr:x>0.9</cdr:x><cdr:y>0.25</cdr:y></cdr:to>'
    '<cdr:sp macro="" textlink=""><cdr:nvSpPr><cdr:cNvPr id="2" name="TextBox 1"/>'
    '<cdr:cNvSpPr txBox="1"/></cdr:nvSpPr><cdr:spPr/>'
    '<cdr:txBody><a:bodyPr/><a:p><a:r><a:t>Drawn inside the chart</a:t></a:r></a:p>'
    "</cdr:txBody></cdr:sp></cdr:relSizeAnchor></c:userShapes>"
)


def _with_shape_in_its_chart(path: Path) -> Path:
    """What Excel writes for a text box drawn with the chart selected: a drawing of the
    chart's own (`chartUserShapes`), not of the sheet."""
    import zipfile

    with zipfile.ZipFile(path) as old:
        parts = {name: old.read(name) for name in old.namelist()}
    chart = "xl/charts/chart1.xml"
    parts[chart] = parts[chart].replace(
        b"</c:chartSpace>",
        b'<c:userShapes xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships" r:id="rIdShapes"/></c:chartSpace>')
    parts["xl/charts/_rels/chart1.xml.rels"] = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
        b'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship '
        b'Id="rIdShapes" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/chartUserShapes" Target="../drawings/drawing9.xml"/></Relationships>')
    parts["xl/drawings/drawing9.xml"] = _USER_SHAPES.encode()
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/drawings/drawing9.xml" ContentType="application/'
        b'vnd.openxmlformats-officedocument.drawingml.chartshapes+xml"/></Types>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as new:
        for name, data in parts.items():
            new.writestr(name, data)
    return path


def test_a_text_box_drawn_inside_a_chart_is_text_the_chart_holds(tmp_path: Path) -> None:
    extracted = ExcelExtractor().extract(_with_shape_in_its_chart(_workbook(tmp_path / "b.xlsx")))

    box = next((node for node in extracted.nodes if node.text == "Drawn inside the chart"), None)
    assert box is not None and box.type is NodeType.TEXT_CHUNK and box.retrievable
    chart = next(node for node in extracted.nodes if node.type is NodeType.CHART)
    assert any(e.type is EdgeType.CONTAINS and e.source_id == chart.id and e.target_id == box.id
               for e in extracted.edges)

"""A worksheet picture is owned by its sheet; what encloses it is binding's question.

The `to` marker of an `editAs="oneCell"` anchor is stale: it can reach into a table the
picture, drawn at its own extent, never touches. Ownership decided from those cells made the
table hold the picture before anything was drawn. Invented workbook.
"""
from __future__ import annotations

from base64 import b64decode
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.worksheet.table import Table

from graphme.adapters.extractors.excel import ExcelExtractor
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.domain.enums import EdgeType, NodeType

PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _workbook(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Orchard"
    for row, values in enumerate((("Tree", "Yield"), ("Pear", 12), ("Plum", 9)), start=8):
        for col, value in enumerate(values, start=4):
            sheet.cell(row=row, column=col, value=value)
    sheet.add_table(Table(displayName="Harvest", ref="D8:E10"))
    picture = Image(BytesIO(PNG))
    picture.width = picture.height = 20  # drawn in the top-left corner, far from the table
    picture.anchor = TwoCellAnchor(
        editAs="oneCell", _from=AnchorMarker(col=0, row=0), to=AnchorMarker(col=4, row=8))
    sheet.add_image(picture)
    workbook.save(path)
    return path


def test_a_picture_whose_stale_anchor_reaches_a_table_is_held_by_its_sheet(
    tmp_path: Path,
) -> None:
    extracted = ExcelExtractor(asset_store=LocalAssetStore(tmp_path / "assets")).extract(
        _workbook(tmp_path / "orchard.xlsx"))

    image = next(node for node in extracted.nodes if node.type is NodeType.IMAGE)
    sheet = next(node for node in extracted.nodes if node.type is NodeType.WORKSHEET)
    owners = {edge.target_id: edge.source_id for edge in extracted.edges
              if edge.type is EdgeType.CONTAINS}
    assert owners[image.id] == sheet.id

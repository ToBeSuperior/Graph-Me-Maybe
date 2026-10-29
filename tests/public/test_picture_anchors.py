"""A picture placed by a start cell and a size (`oneCellAnchor`) with no position of its own
in its shape properties -- how openpyxl writes one -- is drawn by the pinned XLSX viewer only
once that position is written from the anchor's size. Invented workbooks."""
from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from graphme.adapters.rendering.picture_anchors import positioned_pictures

XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"xdr": XDR, "a": A}
FROM = "<from><col>1</col><colOff>0</colOff><row>2</row><rowOff>0</rowOff></from>"


def _pic(xfrm: str = "") -> str:
    return (f'<pic><nvPicPr><cNvPr id="1" name="Image 1"/><cNvPicPr/></nvPicPr>'
            f'<blipFill><a:blip xmlns:a="{A}" r:embed="rId1" xmlns:r="urn:r"/></blipFill>'
            f'<spPr>{xfrm}<a:prstGeom xmlns:a="{A}" prst="rect"/></spPr></pic>')


def _book(path: Path, anchor: str) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/drawings/drawing1.xml", f'<wsDr xmlns="{XDR}">{anchor}</wsDr>')
    return path


def _xfrms(path: Path) -> list[tuple[str | None, ...]]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("xl/drawings/drawing1.xml"))
    return [(x.find("a:off", NS).get("x"), x.find("a:off", NS).get("y"),
             x.find("a:ext", NS).get("cx"), x.find("a:ext", NS).get("cy"))
            for x in root.iterfind(".//xdr:pic/xdr:spPr/a:xfrm", NS)]


def test_a_one_cell_picture_takes_its_position_from_the_anchor_size(tmp_path: Path) -> None:
    source = _book(tmp_path / "invented.xlsx", f'<oneCellAnchor>{FROM}<ext cx="2133600" '
                   f'cy="1066800"/>{_pic()}<clientData/></oneCellAnchor>')

    staged = positioned_pictures(source, tmp_path / "work")

    assert staged != source
    assert _xfrms(staged) == [("0", "0", "2133600", "1066800")]
    assert _xfrms(source) == []
    with zipfile.ZipFile(staged) as archive:
        assert archive.read("xl/workbook.xml") == b"<workbook/>"


def test_a_picture_that_needs_nothing_is_drawn_from_the_file_itself(tmp_path: Path) -> None:
    placed = f'<a:xfrm xmlns:a="{A}"><a:off x="5" y="5"/><a:ext cx="9" cy="9"/></a:xfrm>'
    with_xfrm = _book(tmp_path / "placed.xlsx", f'<oneCellAnchor>{FROM}<ext cx="1" cy="1"/>'
                      f'{_pic(placed)}<clientData/></oneCellAnchor>')
    two_cell = _book(tmp_path / "two.xlsx", f'<twoCellAnchor>{FROM}<to><col>4</col><colOff>0'
                     f'</colOff><row>7</row><rowOff>0</rowOff></to>{_pic()}<clientData/>'
                     f'</twoCellAnchor>')
    not_office = tmp_path / "invented.pdf"
    not_office.write_bytes(b"%PDF-1.4")

    for source in (with_xfrm, two_cell, not_office):
        assert positioned_pictures(source, tmp_path / "work") == source

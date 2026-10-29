"""A worksheet picture the pinned XLSX viewer leaves out made into one it draws.

A picture may be placed by a start cell and a size (`oneCellAnchor`), and its shape properties
need not say where it is -- `<a:xfrm>` is optional, and openpyxl leaves it out. The viewer reads
pictures from `twoCellAnchor` only and sends a `oneCellAnchor` one through the shape path,
which skips a picture with no `<a:xfrm>`: it vanished without an error. In a copy each such
picture is given the position its anchor already states, which is where Excel draws it.
Upstream fixed it in d7f60b865; drop this once `UPSTREAM` pins a commit that has it.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree  # type: ignore[import-untyped]

XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS = {"xdr": XDR, "a": A}
_DRAWING = re.compile(r"xl/drawings/drawing\d+\.xml")


def positioned_pictures(source: Path, workspace: Path) -> Path:
    """The file to draw: `source` itself, or a copy under `workspace` whose one-cell pictures
    with no position of their own carry their anchor's size as it."""
    try:
        with zipfile.ZipFile(source) as archive:
            changed = {item.filename: placed for item in archive.infolist()
                       if _DRAWING.fullmatch(item.filename)
                       and (placed := _placed(archive.read(item))) is not None}
    except (OSError, zipfile.BadZipFile):
        return source  # not an Office file: drawn as it is
    if not changed:
        return source
    staged = workspace / "pictures" / source.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
            staged, "w", compression=zipfile.ZIP_DEFLATED) as copy:
        for item in archive.infolist():
            copy.writestr(item, changed.get(item.filename) or archive.read(item))
    return staged


def _placed(xml: bytes) -> bytes | None:
    """`xml` with an `<a:xfrm>` sized by its anchor on each one-cell picture lacking one; None
    when there is none."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    placed = False
    for anchor in root.iterfind("xdr:oneCellAnchor", _NS):
        size = anchor.find("xdr:ext", _NS)
        properties = anchor.find("xdr:pic/xdr:spPr", _NS)
        if size is None or properties is None or properties.find("a:xfrm", _NS) is not None:
            continue
        xfrm = etree.Element(f"{{{A}}}xfrm")
        etree.SubElement(xfrm, f"{{{A}}}off", x="0", y="0")
        etree.SubElement(xfrm, f"{{{A}}}ext", cx=size.get("cx", "0"), cy=size.get("cy", "0"))
        properties.insert(0, xfrm)  # first, where the schema puts it
        placed = True
    if not placed:
        return None
    written: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                    standalone=True)
    return written

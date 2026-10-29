"""A chart the published viewer paints as "Unsupported chart" made into one it draws.

Excel writes the common "columns on the secondary axis, line on the primary" combo with both
category axes at the bottom, the secondary one deleted. The published viewer (it draws DOCX and
PPTX; the XLSX build carries its own fix) leaves two category axes on one side unresolved and
paints the whole chart as unsupported. A deleted axis has no layout of its own and Office draws
its groups against the categories of the axis beside it, so in a copy the groups that use it
are moved onto that axis and the hidden one dropped: what is drawn is what Office draws.
Upstream: yukiyokotani/office-open-xml-viewer#1984; drop this once a published version has it.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree  # type: ignore[import-untyped]

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS = {"c": C}
_CHART = re.compile(r"(word|ppt|xl)/charts/chart\d+\.xml")


def hidden_axes_merged(source: Path, workspace: Path) -> Path:
    """The file to draw: `source` itself, or a copy under `workspace` whose charts with a
    deleted category axis beside another on the same side draw on that other one."""
    try:
        with zipfile.ZipFile(source) as archive:
            changed = {item.filename: merged for item in archive.infolist()
                       if _CHART.fullmatch(item.filename)
                       and (merged := _merged(archive.read(item))) is not None}
    except (OSError, zipfile.BadZipFile):
        return source  # not an Office file: drawn as it is
    if not changed:
        return source
    staged = workspace / "charts" / source.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
            staged, "w", compression=zipfile.ZIP_DEFLATED) as copy:
        for item in archive.infolist():
            copy.writestr(item, changed.get(item.filename) or archive.read(item))
    return staged


def _merged(xml: bytes) -> bytes | None:
    """`xml` with each deleted category axis that shares its side with exactly one other
    category axis folded into that one; None when there is none."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    plot = root.find(".//c:plotArea", _NS)
    if plot is None:
        return None
    axes = plot.findall("c:catAx", _NS)
    folded = False
    for hidden in [axis for axis in axes if _deleted(axis)]:
        beside = [axis for axis in axes
                  if axis is not hidden and not _deleted(axis) and _side(axis) == _side(hidden)]
        hidden_id, kept_id = _id(hidden), _id(beside[0]) if len(beside) == 1 else None
        if hidden_id is None or kept_id is None:
            continue
        for reference in [*plot.iter(f"{{{C}}}axId"), *plot.iter(f"{{{C}}}crossAx")]:
            if reference.getparent() is not hidden and reference.get("val") == hidden_id:
                reference.set("val", kept_id)
        plot.remove(hidden)
        folded = True
    if not folded:
        return None
    written: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                    standalone=True)
    return written


def _deleted(axis: etree._Element) -> bool:
    node = axis.find("c:delete", _NS)
    return node is not None and node.get("val") in ("1", "true")


def _side(axis: etree._Element) -> str | None:
    node = axis.find("c:axPos", _NS)
    return None if node is None else node.get("val")


def _id(axis: etree._Element) -> str | None:
    node = axis.find("c:axId", _NS)
    return None if node is None else node.get("val")

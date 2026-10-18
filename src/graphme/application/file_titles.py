"""What a file says it is called: the title its properties give, and the words it opens with.

A PDF keeps a title in its metadata and an Office file in `docProps/core.xml`. Neither is
always the title -- an authoring tool leaves "PowerPoint Presentation" there unasked -- so a
title is taken as the document's own only where the document's opening page writes it
(`stated_titles`). The opening words name a file carried inside another that has no name of
its own (`embedded_documents.py`).
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pymupdf

from graphme.application.text_match import encodable, letters

RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_RID = f"{{{OFFICE_REL}}}id"
LISTS = {
    ".pptx": ("ppt/presentation.xml",
              "{http://schemas.openxmlformats.org/presentationml/2006/main}sldId"),
    ".xlsx": ("xl/workbook.xml",
              "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"),
}
"""Where a deck lists its slides and a workbook its sheets, in their order."""
_DC_TITLE = "{http://purl.org/dc/elements/1.1/}title"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OPENING_LINES = 100
"""How far into a file its opening reaches: a title stands at the start."""

File = Path | bytes
"""A file on disk, or one's bytes."""


def rels_part(part: str) -> str:
    """Where a part's relationships are kept."""
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def relations(package: zipfile.ZipFile, rels: str) -> list[tuple[str, str, str, str]]:
    """(owner part, id, relationship type, target part) for each internal relationship."""
    folder, file = posixpath.split(rels)
    owner = posixpath.join(posixpath.dirname(folder), file.removesuffix(".rels"))
    return [
        (owner, relation.get("Id", ""), relation.get("Type", ""), posixpath.normpath(
            posixpath.join(posixpath.dirname(owner), relation.get("Target", ""))).lstrip("/"))
        for relation in ElementTree.fromstring(package.read(rels)).iter(RELS)
        if relation.get("TargetMode") != "External"
    ]


def listed(package: zipfile.ZipFile, main: str, tag: str) -> list[tuple[str, str]]:
    """The parts `main` lists under `tag`, in its order, each with the name it gives it."""
    by_id = {rid: part for _, rid, _, part in relations(package, rels_part(main))}
    return [(by_id[each.get(_RID, "")], each.get("name", ""))
            for each in ElementTree.fromstring(package.read(main)).iter(tag)
            if each.get(_RID) in by_id]


def declared_title(file: File, suffix: str) -> str | None:
    """The title in the file's properties, as written; None when it leaves it empty."""
    try:
        if suffix.lower() == ".pdf":
            with _pdf(file) as pdf:
                title = encodable((pdf.metadata or {}).get("title") or "")
        else:
            with _zip(file) as package:
                if "docProps/core.xml" not in package.namelist():
                    return None
                found = ElementTree.fromstring(package.read("docProps/core.xml")).find(_DC_TITLE)
                title = found.text or "" if found is not None else ""
    except (OSError, RuntimeError, ValueError, KeyError, SyntaxError, zipfile.BadZipFile):
        return None
    return " ".join(str(title).split()) or None


def opening_lines(file: File, suffix: str) -> list[str]:
    """The words a file opens with, line by line: a PDF's first page, a deck's first slide,
    a workbook's first sheet, a Word file's first paragraphs."""
    suffix = suffix.lower()
    try:
        if suffix == ".pdf":
            with _pdf(file) as pdf:
                lines = encodable(pdf[0].get_text()).splitlines() if pdf.page_count else []
        else:
            with _zip(file) as package:
                lines = _package_lines(package, suffix)
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, SyntaxError,
            zipfile.BadZipFile):
        return []
    return [line.strip() for line in lines if line.strip()][:OPENING_LINES]


def stated_titles(file: File, suffix: str) -> tuple[str, ...]:
    """The title in the file's properties, where the file's opening writes it -- every part of
    it, a "series: title" written on two lines as well -- and nothing otherwise."""
    title = declared_title(file, suffix)
    if title is None:
        return ()
    opening = letters(" ".join(opening_lines(file, suffix)))
    parts = [letters(part) for part in title.split(":")]
    return (title,) if all(part in opening for part in parts if part) else ()


def _pdf(file: File) -> pymupdf.Document:
    if isinstance(file, bytes):
        return pymupdf.open(stream=file, filetype="pdf")  # type: ignore[no-untyped-call]
    return pymupdf.open(file)  # type: ignore[no-untyped-call]


def _zip(file: File) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(file) if isinstance(file, bytes) else file)


def _package_lines(package: zipfile.ZipFile, suffix: str) -> list[str]:
    if suffix == ".docx":
        return _texts(package, "word/document.xml", _W + "p", _W + "t")
    parts = listed(package, *LISTS[suffix]) if suffix in LISTS else []
    if not parts:
        return []
    if suffix == ".pptx":
        return _texts(package, parts[0][0], _A + "p", _A + "t")
    return _cells(package, parts[0][0])


def _texts(package: zipfile.ZipFile, part: str, block: str, run: str) -> list[str]:
    """Each paragraph's words, a line break inside one (`a:br`, `w:br`) starting a new line.
    Read only as far as OPENING_LINES lines: a paragraph is a slot taken when it opens, so the
    order is its document's even where one holds another."""
    breaks = (_A + "br", _W + "br")
    paragraphs: list[str] = []
    open_slots: list[int] = []
    lines = 0
    with package.open(part) as file:
        for event, each in ElementTree.iterparse(file, ("start", "end")):
            if each.tag != block:
                continue
            if event == "start":
                open_slots.append(len(paragraphs))
                paragraphs.append("")
                continue
            text = "".join("\n" if t.tag in breaks else t.text or "" for t in each.iter()
                           if t.tag == run or t.tag in breaks)
            paragraphs[open_slots.pop()] = text
            lines += sum(1 for line in text.split("\n") if line.strip())
            if not open_slots:
                each.clear()
                if lines >= OPENING_LINES:
                    break
    return "\n".join(paragraphs).split("\n")


def _cells(package: zipfile.ZipFile, sheet: str) -> list[str]:
    """A sheet's cells holding words, in order: shared strings, and ones written in the cell.
    Only the shared strings those cells name are read."""
    found: list[str | int] = []  # a shared string is its index until read
    with package.open(sheet) as file:
        for _, cell in ElementTree.iterparse(file):
            if cell.tag != _X + "c":
                continue
            kind, value = cell.get("t"), cell.find(_X + "v")
            number = value.text or "" if value is not None else ""
            if kind == "s" and number.isdigit():
                found.append(int(number))
            elif kind == "inlineStr":
                found.append("".join(t.text or "" for t in cell.iter(_X + "t")))
            elif kind == "str":
                found.append(number)
            cell.clear()
            if len(found) >= OPENING_LINES:
                break
    wanted = {each for each in found if isinstance(each, int)}
    shared: dict[int, str] = {}
    if wanted and "xl/sharedStrings.xml" in package.namelist():
        with package.open("xl/sharedStrings.xml") as file:
            seen = 0
            for _, each in ElementTree.iterparse(file):
                if each.tag != _X + "si":
                    continue
                if seen in wanted:
                    shared[seen] = "".join(t.text or "" for t in each.iter(_X + "t"))
                each.clear()
                seen += 1
                if seen > max(wanted):
                    break
    return [shared[each] if isinstance(each, int) else each
            for each in found if not isinstance(each, int) or each in shared]

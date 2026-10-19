"""Documents carried inside a document: a Word file dropped onto a slide, a PDF in a memo, a
workbook attached to a PDF.

The page shows only an icon -- so neither the words nor the picture of the page say what is
in the file. It is read as a document of its own, put in the graph beside the one that
carries it, and the carrier cites it the way a page cites any other document: an
`external_document` node, pointed at by `REFERENCES` from where it sits -- the slide, the
sheet, the document itself -- and joined to the embedded document across the corpus by its
file name (`application/corpus_links.py`).

Where Office keeps such a file:
- an OOXML file whole, as a package part (`ppt/embeddings/...docx`, relationship `package`);
- anything else -- a PDF -- in an OLE compound file (`oleObject*.bin`, `oleObject`), as an
  OLE Packager stream (`\\x01Ole10Native`, which holds the file's own name) or the file's
  bytes alone (`CONTENTS`, `Package`).
A PDF keeps attachments as file-attachment annotations on a page and in its `EmbeddedFiles`.

What is not one of the formats read here (a .doc, a .zip) is cited all the same, so the
graph says it is there, but not read.

A file kept whole as a package part has no name of its own: Office names the part after the
program ("Microsoft_Word___.docx" from a Korean Office's "Microsoft_Word_문서"). Such a file is
named by its title -- the one its properties give, or its first line.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

import olefile
import pymupdf

from graphme.application.corpus_links import embedded_digest, embedded_name
from graphme.application.document_references import cite
from graphme.application.file_titles import (
    LISTS,
    OFFICE_REL,
    declared_title,
    listed,
    opening_lines,
    relations,
)
from graphme.application.ingest import SUFFIX_FORMATS
from graphme.domain.enums import NodeType
from graphme.domain.models import ExtractedDocument

_log = logging.getLogger(__name__)

_REL = OFFICE_REL + "/"
PACKAGE, OLE_OBJECT = _REL + "package", _REL + "oleObject"
_MAIN_PARTS = {"word/document.xml": ".docx", "xl/workbook.xml": ".xlsx",
               "ppt/presentation.xml": ".pptx"}
MAX_EMBEDDED_BYTES = 200 * 1024 * 1024
"""A carried file larger than this -- as large as the demo takes an upload -- is cited by its
name and not read: a few hundred kilobytes of package can hold gigabytes."""
NAME_BYTES = 180
"""How long a carried file's name may be, in UTF-8: room for the digest and a suffix under the
255 bytes most file systems allow."""
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f\u200b-\u200f\ufeff\u202a-\u202e\u2066-\u2069]+')
"""What a name may not hold: path separators, characters Windows refuses, control characters,
and the marks that turn text around ("txt.exe" shown as "exe.txt")."""


@dataclass(frozen=True, slots=True)
class Embedded:
    """One file carried in a document: where it sits (`owner`: the part it hangs off, or
    `page:N` in a PDF, or "" for the document as a whole), where it is kept, its bytes, and
    the name it is read under (`embedded_name`): its own name and a digest of its bytes, so
    two different "Microsoft_Word.docx" are two documents, and the same file carried twice
    -- under whatever name -- is one, which every citation of it finds."""

    owner: str
    part: str
    name: str
    content: bytes
    named: bool = True
    """Whether `name` is the file's own, or only what the place it is kept is called."""
    kept: bool = True
    """Whether its bytes were taken: one larger than MAX_EMBEDDED_BYTES is cited, not read."""

    @property
    def readable(self) -> bool:
        return self.kept and PurePosixPath(self.name).suffix.lower() in SUFFIX_FORMATS


def name_part(text: str, fallback: str) -> str:
    """One part of a path, from words someone typed: nothing that leaves it or misleads the eye
    (`_UNSAFE`), no leading or trailing dot or space, `fallback` when nothing is left."""
    return " ".join(_UNSAFE.sub("", text).split()).strip(". ") or fallback


def safe_name(name: str) -> str:
    """A file name fit to write on any system: its last path part, without what a file system
    or a reader's eye trips on, and no longer than NAME_BYTES."""
    own = name.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = PurePosixPath(own).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix):
        suffix = ""
    stem = " ".join(_UNSAFE.sub(" ", own[: len(own) - len(suffix)]).split()).strip(" .")
    if len(stem.encode()) > NAME_BYTES:  # its start and its end, which tell names apart
        head, tail = stem[: len(stem) // 2], stem[len(stem) // 2 :]
        while len(f"{head}…{tail}".encode()) > NAME_BYTES:
            head, tail = head[:-1], tail[1:]
        stem = f"{head}…{tail}"
    return (stem or "file") + suffix


def embedded_in(source: Path) -> list[Embedded]:
    """The readable files a document carries, in the order they are kept; one level only."""
    return [each for each in carried_in(source) if each.readable]


_NAMED: dict[tuple[str, int, int], list[Embedded]] = {}
"""Each carrier already opened, as it was found without its bytes: the build that unpacks a
document's files and the one that cites them open it once between them. Keyed by where the
file is and when it last changed."""
NAMED_LIMIT = 4096
"""How many carriers `_NAMED` holds before it starts over."""


def carried_in(source: Path, *, keep: bool = True) -> list[Embedded]:
    """Every file a document carries, read here or not, each under the name it is read by.
    Without `keep` their bytes are named by and let go as each is found, not held."""
    suffix = source.suffix.lower()
    carrier = ""

    def finish(each: Embedded) -> Embedded:
        nonlocal carrier
        each = replace(each, name=safe_name(each.name))
        if not each.kept:
            _log.warning("%s: embedded %s cited, not read -- larger than %d bytes",
                         source.name, each.name, MAX_EMBEDDED_BYTES)
        elif not each.readable:
            _log.info("%s: embedded %s cited, not read -- not a format read here",
                      source.name, each.name)
        elif not each.named and (title := _title(each.content, PurePosixPath(each.name).suffix)):
            each = replace(each, name=title + PurePosixPath(each.name).suffix.lower())
        # A file not kept has no bytes to name it by: where it sits -- its carrier's bytes and
        # its part, not the carrier's path -- stands in, so two such files are not one, nor
        # one with an empty file elsewhere, and a carrier moved keeps the same name.
        if not each.kept:
            carrier = carrier or _digest_of(source)
        named_by = each.content if each.kept else f"{carrier}|{each.part}".encode()
        each = replace(each, name=embedded_name(each.name, named_by))
        return each if keep else replace(each, content=b"")

    try:
        if suffix == ".pdf":
            carried = _in_pdf(source, finish)
        elif suffix in SUFFIX_FORMATS:
            carried = _in_package(source, finish)
        else:
            return []
    except Exception as error:  # noqa: BLE001 -- a file nobody can open carries nothing here
        _log.warning("%s: its embedded files not read -- %s", source.name, error)
        return []
    if len(_NAMED) >= NAMED_LIMIT:
        _NAMED.clear()
    _NAMED[_key(source)] = [replace(each, content=b"") for each in carried]
    return carried


def _key(source: Path) -> tuple[str, int, int]:
    stat = source.stat()
    return str(source), stat.st_mtime_ns, stat.st_size


def _digest_of(source: Path) -> str:
    with source.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def unpack_embedded(sources: list[Path], into: Path) -> list[Path]:
    """`sources` and, after them, every file they carry written under `into` -- once, however
    many carriers hold it under however many names."""
    unpacked: dict[str, Path] = {}
    for source in sources:
        for each in embedded_in(source):
            key = embedded_digest(each.name) or each.name
            if key in unpacked:
                continue
            target = into / each.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(each.content)
            unpacked[key] = target
    return [*sources, *unpacked.values()]


def cite_embedded(extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
    """A node for every file the document carries, cited from where it sits."""
    try:
        carried = _NAMED.get(_key(source))
        if carried is None:
            carried = carried_in(source, keep=False)
    except OSError:
        return extracted
    if not carried:
        return extracted
    sits_on = _owners(extracted, source)
    return cite(extracted, [
        (sits_on.get(each.owner, extracted.document.id), each.name,
         {"named_as": each.name, "kind": "embedded", "part": each.part, "sits_on": each.owner})
        for each in carried
    ])


# ---- where files are kept: each under its own name, named for the graph by `embedded_in` --
def _in_package(source: Path, finish: Callable[[Embedded], Embedded]) -> list[Embedded]:
    found: list[Embedded] = []
    taken: dict[str, Embedded] = {}
    cited: set[tuple[str, str]] = set()
    with zipfile.ZipFile(source) as package:
        names = set(package.namelist())
        for rels in sorted((name for name in names if name.endswith(".rels")), key=_natural):
            try:
                links = relations(package, rels)
            except Exception as error:  # noqa: BLE001 -- one broken part is that part
                _log.warning("%s: %s not read -- %s", source.name, rels, error)
                continue
            for owner, _, kind, part in links:
                # A chart keeps its data as a workbook of its own: the chart's, not a file
                # someone put there.
                if kind not in (PACKAGE, OLE_OBJECT) or part not in names or "/charts/" in owner:
                    continue
                if part in taken:  # one file on two slides is cited from both
                    if (part, owner) not in cited:
                        cited.add((part, owner))
                        found.append(replace(taken[part], owner=owner))
                    continue
                try:
                    each = _kept(package, owner, part, kind)
                except Exception as error:  # noqa: BLE001 -- one broken object is that object
                    _log.warning("%s: embedded %s not read -- %s", source.name, part, error)
                    continue
                if each is None:
                    _log.info("%s: embedded %s passed over -- an object, not a file",
                              source.name, part)
                    continue
                each = finish(each)
                taken[part] = each
                cited.add((part, owner))
                found.append(each)
    return found


def _kept(package: zipfile.ZipFile, owner: str, part: str, kind: str) -> Embedded | None:
    own = PurePosixPath(part)
    if package.getinfo(part).file_size > MAX_EMBEDDED_BYTES:
        return Embedded(owner, part, own.name, b"", named=False, kept=False)
    blob = package.read(part)
    if kind == PACKAGE:
        return Embedded(owner, part, own.name, blob, named=False)
    inside = _ole_file(blob, own.stem)
    return Embedded(owner, part, *inside) if inside is not None else None


def _natural(name: str) -> list[int | str]:
    """"slide2" before "slide10": a deck's own order, as far as its part names tell it."""
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r"(\d+)", name)]


_LEGACY = {"WordDocument": ".doc", "Workbook": ".xls", "Book": ".xls",
           "PowerPoint Document": ".ppt"}
"""The stream that makes a compound file a pre-2007 Office file, kept whole as the object."""


def _ole_file(blob: bytes, stem: str) -> tuple[str, bytes, bool] | None:
    """The file an OLE object holds -- by its own name where the object keeps one -- and
    whether that name is its own."""
    if not olefile.isOleFile(blob):
        return None
    with olefile.OleFileIO(blob) as ole:
        if ole.exists("\x01Ole10Native"):
            return _packaged(ole.openstream("\x01Ole10Native").read())
        for stream in ("CONTENTS", "Package"):
            if ole.exists(stream):
                data = ole.openstream(stream).read()
                suffix = _sniffed(data)
                return (f"{stem}{suffix}", data, False) if suffix else None
        legacy = next((suffix for stream, suffix in _LEGACY.items() if ole.exists(stream)), None)
    return (f"{stem}{legacy}", blob, False) if legacy else None


def _packaged(stream: bytes) -> tuple[str, bytes, bool] | None:
    """An OLE Packager stream: its size, then the file's name, where it came from, a temp
    path, and the file -- the layout Windows' Packager writes. The names are in the system's
    code page; a newer Packager writes them again in UTF-16 after the file, which is read
    first, so a Korean name is not taken for Latin-1."""
    try:
        body, at = stream[4:], 2

        def text() -> bytes:
            nonlocal at
            end = body.index(b"\0", at)
            value, at = body[at:end], end + 1
            return value

        name = text()
        text()  # where it came from
        at += 4  # two reserved words
        (length,) = struct.unpack_from("<I", body, at)
        at += 4 + length  # the temp path
        (size,) = struct.unpack_from("<I", body, at)
        data = body[at + 4 : at + 4 + size]
        label = _wide_label(body[at + 4 + size :]) or _ansi(name)
        return PurePosixPath(label.replace("\\", "/")).name, data, True
    except (ValueError, struct.error):
        return None


def _wide_label(tail: bytes) -> str | None:
    """The UTF-16 name after a Packager's file: the temp path, then the name, each a count
    of characters and the characters."""
    try:
        (temp,) = struct.unpack_from("<I", tail, 0)
        at = 4 + 2 * temp
        (chars,) = struct.unpack_from("<I", tail, at)
        label = tail[at + 4 : at + 4 + 2 * chars].decode("utf-16-le").rstrip("\0")
    except (struct.error, UnicodeDecodeError):
        return None
    return label or None


def _ansi(raw: bytes) -> str:
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _sniffed(data: bytes) -> str | None:
    """A file's format by its first bytes: a PDF, or an OOXML package by its main part."""
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                names = set(package.namelist())
        except zipfile.BadZipFile:
            return None
        return next((suffix for part, suffix in _MAIN_PARTS.items() if part in names), None)
    return None


def _in_pdf(source: Path, finish: Callable[[Embedded], Embedded]) -> list[Embedded]:
    found: list[Embedded] = []

    def add(each: Embedded | None) -> None:
        if each is not None:
            found.append(finish(each))

    with pymupdf.open(source) as pdf:  # type: ignore[no-untyped-call]
        for page in pdf:
            for annot in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]):  # type: ignore[attr-defined]
                info = annot.file_info
                own = info.get("filename")
                add(_attached(source, f"page:{page.number + 1}", f"annot:{annot.xref}",
                              own or f"attachment{annot.xref}", info, annot.get_file,
                              named=bool(own)))
        for key in pdf.embfile_names():
            info = pdf.embfile_info(key)
            add(_attached(source, "", f"embedded:{_repaired(key)}",
                          info.get("ufilename") or info.get("filename") or key, info,
                          partial(pdf.embfile_get, key)))
    return found


def _attached(source: Path, owner: str, part: str, name: str, info: dict[str, Any],
              read: Callable[[], bytes], *, named: bool = True) -> Embedded | None:
    try:
        if int(info.get("size") or info.get("length") or 0) > MAX_EMBEDDED_BYTES:
            return Embedded(owner, part, _repaired(name), b"", named, kept=False)
        # The size a PDF declares is the writer's word: what was read is measured as well.
        # ponytail: the bytes are read whole before measuring; pymupdf reads no part of one.
        data = read()
        if len(data) > MAX_EMBEDDED_BYTES:
            return Embedded(owner, part, _repaired(name), b"", named, kept=False)
        return Embedded(owner, part, _repaired(name), data, named)
    except Exception as error:  # noqa: BLE001 -- one attachment nobody can read is that one
        _log.warning("%s: attachment %s not read -- %s", source.name, name, error)
        return None


def _repaired(name: str) -> str:
    """A name some writers put in a PDF as UTF-8 bytes, which reads back as Latin-1: "ì¶\x9c"
    for "출". Read as what it was written in, where that is what it was."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


# ---- what a file without a name of its own is called ---------------------------------------
TITLE_CHARS = 60
"""A title longer than this is cut: a first line can be a whole sentence."""


def _title(content: bytes, suffix: str) -> str | None:
    """The title a file gives itself -- in its properties, or as its first line of text --
    made fit to be a file name; None when it gives none."""
    title = declared_title(content, suffix) or next(iter(opening_lines(content, suffix)), "")
    title = " ".join(_UNSAFE.sub(" ", title).split()).strip(" .")[:TITLE_CHARS].strip()
    return title or None


# ---- what cites it ------------------------------------------------------------------------
def _owners(extracted: ExtractedDocument, source: Path) -> dict[str, str]:
    """Each place a file can sit, as the node that stands for it: a slide's part its slide, a
    worksheet's part its sheet. Anything else is cited by the document."""
    suffix = source.suffix.lower()
    if suffix not in LISTS:
        return {}
    with zipfile.ZipFile(source) as package:
        parts = listed(package, *LISTS[suffix])
    if suffix == ".pptx":  # a slide is known by its place in the deck, not its part's name
        slides = {node.locator.slide: node.id for node in extracted.nodes
                  if node.type is NodeType.SLIDE}
        return {part: slides[number] for number, (part, _) in enumerate(parts, start=1)
                if number in slides}
    sheets = {node.locator.sheet: node.id for node in extracted.nodes
              if node.type is NodeType.WORKSHEET}
    return {part: sheets[name] for part, name in parts if name in sheets}


_DIGEST = re.compile(r"\.emb[0-9a-f]{8}(?=\.[A-Za-z0-9]{1,8}\b)")


def shown_name(text: str) -> str:
    """File names as a reader knows them: an attachment's digest (`embedded_name`) tells two
    files apart for the program and stays in the API, but means nothing printed."""
    return _DIGEST.sub("", text)

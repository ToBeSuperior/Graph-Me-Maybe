"""A Hangul document (HWP or HWPX) printed to the PDF it is read as.

    python -m graphme.adapters.rendering.hwp_print SOURCE OUT

The viewer bundle's HWP renderer (`hwp/`, the published @rhwp/core build) draws each page in a
browser and the browser prints them. From there the document is a PDF like any other: drawn,
bound, outlined and previewed by what reads PDFs, with nothing of its own. The print keeps the
document's name, so the graph and the reader still see the file they gave; PyMuPDF opens a PDF
by its contents, whatever it is called.

The renderer does not draw everything: some table cells come out empty, and a wide table on a
landscape page is cut at the page's edge. So the file's own paragraphs are read beside the print
(HWPX's section XML, HWP's paragraph-text records) and those the print does not hold are set on
pages after it (`keep_undrawn`): placed where the renderer would not draw them is not possible,
but read they are.

Run in a process of its own so the caller's timeout can stop it. DIST_ROOT names the viewer
bundle; BROWSER_PATH, when set, the Chrome to draw with (else Playwright's own browser).
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import zipfile
import zlib
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from graphme import processes
from graphme.adapters.rendering.browser_capture import NO_BROWSER, SiteHandler, font_environment
from graphme.domain.enums import NodeType
from graphme.domain.ids import sha256_file
from graphme.domain.models import ExtractedDocument

HWP_SUFFIXES = (".hwp", ".hwpx")
PAGE = Path(__file__).with_name("hwp_print.html")
DEFAULT_TIMEOUT_SECONDS = 300.0

HWP_COMPRESSED, HWP_PASSWORD, HWP_DISTRIBUTION = 1, 2, 4  # FileHeader's properties
HWPTAG_PARA_TEXT = 67  # HWPTAG_BEGIN (16) + 51
TAB = 9
WIDE_CONTROLS = frozenset({*range(1, 10), 11, 12, *range(14, 24)})  # eight code units each
SPACES = frozenset({30, 31})  # a space that does not break, one of fixed width
UNCOMPARED = re.compile(r"[\s­-\U000f0000-\U0010ffff]+")
MIN_COMPARED = 4
UNDRAWN_MARGIN = 56
UNDRAWN_POINTS = 10


def steady(pdf: Path) -> Path:
    """The print without the moment it was made: the browser stamps it, and the same file
    printed twice was otherwise two documents."""
    import pymupdf

    with pymupdf.open(pdf) as document:  # type: ignore[no-untyped-call]
        document.set_metadata({})
        document.xref_set_key(-1, "ID", "null")  # a file identifier is made new at each save
        content = document.tobytes(garbage=3, deflate=True, no_new_id=True)
    pdf.write_bytes(content)
    return pdf


def as_original(extracted: ExtractedDocument, original: Path) -> ExtractedDocument:
    """A print's reading recorded as the file it was printed from: where it is, so its pages
    are drawn from it again, and its content, which is how the space knows it is in already."""
    digest = sha256_file(original)
    record = extracted.document.model_copy(
        update={"source_path": original, "content_hash": digest})
    nodes = tuple(node.model_copy(update={"content_hash": digest})
                  if node.type is NodeType.DOCUMENT else node for node in extracted.nodes)
    return extracted.model_copy(update={"document": record, "nodes": nodes})


def file_paragraphs(source: Path) -> list[str]:
    """The document's own paragraphs, a table cell's each its own, in the file's order: what
    the print is checked against. None for a file not read this way (a password, a
    distribution-only document, a damaged one) -- its print is all there is."""
    try:
        if source.suffix.lower() == ".hwpx":
            return _hwpx_paragraphs(source)
        return _hwp_paragraphs(source)
    except (OSError, ValueError, KeyError, zlib.error, struct.error, zipfile.BadZipFile,
            ElementTree.ParseError):
        return []


def _hwpx_paragraphs(source: Path) -> list[str]:
    with zipfile.ZipFile(source) as archive:
        sections = sorted((name for name in archive.namelist()
                           if re.fullmatch(r"Contents/section\d+\.xml", name)),
                          key=lambda name: int(re.sub(r"\D", "", name)))
        roots = [ElementTree.fromstring(archive.read(name)) for name in sections]
    paragraphs = []
    for root in roots:
        for paragraph in root.iter():  # a cell's paragraphs come up as their own
            if paragraph.tag.rpartition("}")[2] != "p":
                continue
            words = []
            for run in paragraph.findall("{*}run"):
                for text in run.findall("{*}t"):
                    words.append(text.text or "")
                    for inner in text:  # a tab or a line break, then what follows it
                        words.append("\t" if inner.tag.endswith("tab") else "")
                        words.append(inner.tail or "")
            paragraphs.append("".join(words))
    return paragraphs


def _hwp_paragraphs(source: Path) -> list[str]:
    import olefile

    with olefile.OleFileIO(str(source)) as ole:
        flags = struct.unpack_from("<I", ole.openstream("FileHeader").read(), 36)[0]
        if flags & (HWP_PASSWORD | HWP_DISTRIBUTION):
            return []
        paragraphs: list[str] = []
        section = 0
        while ole.exists(f"BodyText/Section{section}"):
            data = ole.openstream(f"BodyText/Section{section}").read()
            paragraphs += hwp_record_paragraphs(
                zlib.decompress(data, -15) if flags & HWP_COMPRESSED else data)
            section += 1
    return paragraphs


def hwp_record_paragraphs(data: bytes) -> list[str]:
    """Each paragraph's text in a body section's records (HWP 5): its characters, a tab as a
    tab, and every other control -- one code unit, or eight with what it carries -- left out."""
    paragraphs = []
    at = 0
    while at + 4 <= len(data):
        (head,) = struct.unpack_from("<I", data, at)
        at += 4
        tag, size = head & 0x3FF, head >> 20
        if size == 0xFFF:  # a long record names its size after the header
            (size,) = struct.unpack_from("<I", data, at)
            at += 4
        if tag == HWPTAG_PARA_TEXT:
            units = struct.unpack_from(f"<{size // 2}H", data, at)
            kept: list[int] = []
            index = 0
            while index < len(units):
                unit = units[index]
                if unit in WIDE_CONTROLS:
                    if unit == TAB:
                        kept.append(TAB)
                    index += 8
                    continue
                if unit >= 32:
                    kept.append(unit)
                elif unit in SPACES:
                    kept.append(32)
                index += 1
            paragraphs.append(
                struct.pack(f"<{len(kept)}H", *kept).decode("utf-16-le", "replace"))
        at += size
    return paragraphs


def _squashed(text: str) -> str:
    """Text as compared: no space, no soft hyphen, no private-use bullet a font draws."""
    return UNCOMPARED.sub("", text)


def undrawn(paragraphs: list[str], printed: str) -> list[str]:
    """The paragraphs whose words the print does not hold, in order. One of a few characters
    is passed over: it is found anywhere by chance, and missed it says little."""
    held = _squashed(printed)
    return [paragraph for paragraph in paragraphs
            if len(words := _squashed(paragraph)) >= MIN_COMPARED and words not in held]


def keep_undrawn(source: Path, pdf: Path) -> int:
    """The renderer leaves some of a document out of what it draws -- a table's cells, the
    part of a wide table past the page. What the file holds and the print lacks is set on
    pages after the print, so its words are read with the rest. How many paragraphs."""
    import pymupdf

    with pymupdf.open(pdf) as document:  # type: ignore[no-untyped-call]
        missing = undrawn(file_paragraphs(source),
                          "".join(page.get_text() for page in document))
        if not missing:
            return 0
        # Pretendard's space reads back as a no-break space
        font = pymupdf.Font("korea")  # type: ignore[no-untyped-call]
        paper = pymupdf.paper_rect("a4")
        area = paper + (UNDRAWN_MARGIN, UNDRAWN_MARGIN, -UNDRAWN_MARGIN, -UNDRAWN_MARGIN)
        left = "\n".join(paragraph.replace("\t", " ") for paragraph in missing)
        while left:
            page = document.new_page(width=paper.width, height=paper.height)
            writer = pymupdf.TextWriter(paper)  # type: ignore[no-untyped-call]
            over = writer.fill_textbox(area, left, font=font, fontsize=UNDRAWN_POINTS)
            writer.write_text(page)  # type: ignore[no-untyped-call]
            left = "\n".join(line for line, _ in over)
        content = document.tobytes()
    pdf.write_bytes(content)
    return len(missing)


def print_pdf(source: Path, out: Path, *, bundle: Path, browser_path: str | None) -> None:
    """Draw `source` with the bundle's HWP renderer and print it, every page, to `out`."""
    from playwright.sync_api import Error, sync_playwright

    if not (bundle / "hwp" / "rhwp.js").is_file():  # otherwise the page only says a module failed
        raise RuntimeError(
            f"no HWP renderer in {bundle} (hwp/) -- run the installer again, or build the viewer "
            "with src/graphme/adapters/rendering/viewer/setup.sh; if it is elsewhere, point "
            "GRAPHME_VIEWER_DIR at it.")
    kind = source.suffix.lower().lstrip(".")
    with tempfile.TemporaryDirectory(prefix="graphme-hwp-") as temporary:
        site = Path(temporary)
        shutil.copyfile(source, site / f"sample.{kind}")
        shutil.copyfile(PAGE, site / "render.html")
        handler = functools.partial(SiteHandler, site=site.resolve(), bundle=bundle.resolve())
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with sync_playwright() as playwright:
                    try:
                        browser = playwright.chromium.launch(
                            headless=True, executable_path=browser_path or None,
                            env=cast("dict[str, str | float | bool] | None",
                                     font_environment(site)))
                    except Error as error:
                        if "xecutable doesn't exist" in str(error) or "ENOENT" in str(error):
                            raise RuntimeError(NO_BROWSER) from None
                        raise
                    try:
                        page = browser.new_page()
                        page.goto(f"{origin}/render.html?kind={kind}",
                                  wait_until="domcontentloaded", timeout=120_000)
                        page.wait_for_function("() => window.__ready === true", timeout=300_000)
                        failure = page.evaluate("() => window.__error || null")
                        if failure:
                            raise RuntimeError(failure)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        page.pdf(path=str(out), print_background=True,
                                 prefer_css_page_size=True)
                    finally:
                        browser.close()
            finally:
                server.shutdown()
    keep_undrawn(source, out)
    steady(out)


Run = Callable[..., Any]


class HwpPrinter:
    """Prints a Hangul document to a PDF beside its reading; any other file is left as it is."""

    def __init__(self, *, viewer_directory: Path, chrome_executable: str | None = None,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, run: Run = processes.run
                 ) -> None:
        self._viewer = viewer_directory
        self._chrome = chrome_executable
        self._timeout = timeout_seconds
        self._run = run

    def pdf(self, source: Path, folder: Path) -> Path:
        """`source` as a PDF: printed into `folder` under its own name, if it is HWP or HWPX."""
        if source.suffix.lower() not in HWP_SUFFIXES:
            return source
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / source.name
        # Its messages carry file names: UTF-8 both ways, not Windows' ANSI code page.
        environment = {**os.environ, "DIST_ROOT": str(self._viewer), "PYTHONIOENCODING": "utf-8"}
        if self._chrome:
            environment["BROWSER_PATH"] = self._chrome
        completed = self._run(
            [sys.executable, "-m", "graphme.adapters.rendering.hwp_print", str(source), str(out)],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=self._timeout, env=environment,
        )
        if completed.returncode != 0:
            detail = (completed.stderr.strip() or completed.stdout.strip())[:1_500]
            raise RuntimeError(detail or f"the HWP print exited with {completed.returncode}")
        return out


def main(argv: list[str]) -> int:
    """The error alone on stderr: the caller shows it, and a traceback's lines are not it."""
    try:
        source, out = argv
        print_pdf(Path(source), Path(out), bundle=Path(os.environ.get("DIST_ROOT", ".")),
                  browser_path=os.environ.get("BROWSER_PATH"))
    except Exception as error:  # noqa: BLE001 -- reported to the parent, which raises it
        print(str(error) or type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

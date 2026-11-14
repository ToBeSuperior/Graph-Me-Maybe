"""HWP and HWPX are read as the PDF they print to: drawn by the bundled HWP renderer in a browser,
then read like any PDF. Invented files only; no browser is started."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import fitz
import pytest

from graphme.adapters.rendering import hwp_print
from graphme.adapters.rendering.hwp_print import HwpPrinter, steady
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.application.ingest import format_of
from graphme.domain.enums import DocumentFormat


def _pdf(path: Path, pages: int, date: str = "D:20260101000000Z") -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        document.new_page().insert_text((72, 72), f"Invented page {number}")
    document.set_metadata({"creationDate": date, "modDate": date})
    document.save(path)
    return path


@pytest.mark.parametrize("name", ["memo.hwp", "memo.HWPX"])
def test_a_hangul_document_is_read_as_a_pdf(name: str) -> None:
    assert format_of(Path(name)) is DocumentFormat.PDF


def test_a_print_says_the_same_bytes_whenever_it_is_made(tmp_path: Path) -> None:
    # The browser stamps the time it printed; left in, the same file read twice was two documents.
    first = steady(_pdf(tmp_path / "a.hwp", 2, "D:20260101000000Z"))
    second = steady(_pdf(tmp_path / "b.hwp", 2, "D:20270202020202Z"))

    assert first.read_bytes() == second.read_bytes()
    assert fitz.open(first).page_count == 2


def test_the_printer_leaves_any_other_file_as_it_is(tmp_path: Path) -> None:
    printer = HwpPrinter(viewer_directory=tmp_path, run=lambda *a, **k: pytest.fail("printed"))
    source = _pdf(tmp_path / "invented.pdf", 1)

    assert printer.pdf(source, tmp_path / "out") == source


def test_the_printer_prints_beside_it_under_the_document_s_own_name(tmp_path: Path) -> None:
    asked: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        asked.append(command)
        assert kwargs["env"]["DIST_ROOT"] == str(tmp_path / "viewer")
        _pdf(Path(command[-1]), 1)
        return subprocess.CompletedProcess(command, 0, "", "")

    source = tmp_path / "보고서.hwpx"
    source.write_bytes(b"invented")
    printer = HwpPrinter(viewer_directory=tmp_path / "viewer", run=run)

    printed = printer.pdf(source, tmp_path / "out")

    assert printed == tmp_path / "out" / "보고서.hwpx"
    assert fitz.open(printed).is_pdf
    assert asked[0][-2:] == [str(source), str(printed)]


def test_a_failed_print_says_the_browser_s_own_message(tmp_path: Path) -> None:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "the file is not a Hangul document")

    source = tmp_path / "broken.hwp"
    source.write_bytes(b"invented")

    with pytest.raises(RuntimeError, match="not a Hangul document"):
        HwpPrinter(viewer_directory=tmp_path, run=run).pdf(source, tmp_path / "out")


class _Printer:
    def __init__(self, pages: int) -> None:
        self.pages = pages

    def pdf(self, source: Path, folder: Path) -> Path:
        if source.suffix != ".hwp":
            return source
        folder.mkdir(parents=True, exist_ok=True)
        return _pdf(folder / source.name, self.pages)


def test_the_page_renderer_draws_a_hangul_document_from_its_print(tmp_path: Path) -> None:
    source = tmp_path / "invented.hwp"
    source.write_bytes(b"invented")
    renderer = PageRenderer(browser=PdfPageRenderer(), pdf=PdfPageRenderer(), printer=_Printer(3))

    assert [page.index for page in renderer.render(source, DocumentFormat.PDF)] == [1, 2, 3]
    assert renderer.preview(source, DocumentFormat.PDF, 2).startswith(b"\x89PNG")
    assert sorted(renderer.previews(source, DocumentFormat.PDF, [1, 3])) == [1, 3]


def test_a_missing_hangul_renderer_is_named_before_any_browser_starts(tmp_path: Path) -> None:
    source = tmp_path / "invented.hwp"
    source.write_bytes(b"invented")

    with pytest.raises(RuntimeError, match="setup.sh"):
        hwp_print.print_pdf(source, tmp_path / "out.pdf", bundle=tmp_path, browser_path=None)


def test_a_print_read_is_recorded_as_the_hangul_file_it_came_from(tmp_path: Path) -> None:
    # The space knows a document by its file's content: recorded as the print's, a folder built
    # again read every Hangul document again, and its pages were drawn from a print long gone.
    from graphme.adapters.extractors.pdf import PDFExtractor
    from graphme.adapters.rendering.hwp_print import as_original
    from graphme.application.corpus_build import content_hash
    from graphme.domain.enums import NodeType

    original = tmp_path / "invented.hwp"
    original.write_bytes(b"invented hangul bytes")
    (tmp_path / "print").mkdir()
    printed = _pdf(tmp_path / "print" / "invented.hwp", 1)

    read = as_original(PDFExtractor().extract(printed), original)

    assert read.document.source_path == original
    assert read.document.content_hash == content_hash(original)
    root = next(node for node in read.nodes if node.type is NodeType.DOCUMENT)
    assert root.content_hash == content_hash(original)

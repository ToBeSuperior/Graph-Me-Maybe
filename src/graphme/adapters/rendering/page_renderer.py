"""One way to ask for a document's pages, whatever the document is.

OOXML has to be drawn before anything can be said about where things sit; a PDF is
already drawn and only has to be rastered. Everything downstream -- the crops, the
merge, the hierarchy pass -- works on pages and should not have to know which.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.hwp_print import HWP_SUFFIXES
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.domain.enums import DocumentFormat


class RendersPages(Protocol):
    """Anything that can hand back a document's pages."""

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[RenderedPage, ...]: ...


class PreviewsPages(RendersPages, Protocol):
    """A renderer that can also draw one page for a reader to look at."""

    def preview(self, source: Path, document_format: DocumentFormat, index: int) -> bytes: ...

    def previews(self, source: Path, document_format: DocumentFormat, indexes: Sequence[int]
                 ) -> dict[int, bytes]: ...


class PrintsPdf(Protocol):
    """Something that turns a document into the PDF it is read as (HWP, HWPX)."""

    def pdf(self, source: Path, folder: Path) -> Path: ...


class PageRenderer:
    """Routes each format to the thing that can draw it, and draws each file once.

    A document is drawn for whoever asks -- the binding, the figure pass, the text
    grouping, the outline -- and drawing a slide deck means a browser. So the last file
    drawn is kept, keyed by what it was when it was drawn, and a file that changed under
    the same name is drawn again.
    ponytail: one document's pages held at a time, per-page streaming if a long
    document's renderings stop fitting in memory.
    """

    def __init__(self, *, browser: PreviewsPages, pdf: PreviewsPages | None = None,
                 printer: PrintsPdf | None = None) -> None:
        self._browser = browser
        self._pdf = pdf or PdfPageRenderer()
        self._printer = printer
        self._drawn: tuple[tuple[Any, ...], tuple[RenderedPage, ...]] | None = None
        self._print: tuple[tuple[Any, ...], Path] | None = None

    def printed(self, source: Path, folder: Path) -> Path:
        """`source` as the file its pages are drawn from: a Hangul document's PDF, printed into
        `folder`; anything else as it is."""
        if self._printer is None or source.suffix.lower() not in HWP_SUFFIXES:
            return source
        return self._printer.pdf(source, folder)

    def _readable(self, source: Path) -> Path:
        """`printed`, kept for the last file asked: a reader turning pages prints it once."""
        if self._printer is None or source.suffix.lower() not in HWP_SUFFIXES:
            return source
        stat = source.stat()
        key = (str(source), stat.st_mtime_ns, stat.st_size)
        if self._print is not None and self._print[0] == key and self._print[1].is_file():
            return self._print[1]
        if self._print is not None:
            shutil.rmtree(self._print[1].parent, ignore_errors=True)
        folder = Path(tempfile.mkdtemp(prefix="graphme-print-"))
        self._print = (key, self.printed(source, folder))
        return self._print[1]

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[RenderedPage, ...]:
        stat = source.stat()
        key = (str(source), document_format, stat.st_mtime_ns, stat.st_size)
        if self._drawn is not None and self._drawn[0] == key:
            return self._drawn[1]
        drawn = self._pdf if document_format is DocumentFormat.PDF else self._browser
        pages = tuple(drawn.render(self._readable(source), document_format))
        self._drawn = (key, pages)
        return pages

    def preview(self, source: Path, document_format: DocumentFormat, index: int) -> bytes:
        """Page `index` (from 1) for a reader: that page alone, at a preview's resolution."""
        drawn = self._pdf if document_format is DocumentFormat.PDF else self._browser
        return drawn.preview(self._readable(source), document_format, index)

    def previews(self, source: Path, document_format: DocumentFormat, indexes: Sequence[int]
                 ) -> dict[int, bytes]:
        """The pages `indexes` (from 1) for a reader, at a preview's resolution, drawn in one
        go: those the file has, by index."""
        drawn = self._pdf if document_format is DocumentFormat.PDF else self._browser
        return drawn.previews(self._readable(source), document_format, indexes)

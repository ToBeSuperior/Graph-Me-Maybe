"""One way to ask for a document's pages, whatever the document is.

OOXML has to be drawn before anything can be said about where things sit; a PDF is
already drawn and only has to be rastered. Everything downstream -- the crops, the
merge, the hierarchy pass -- works on pages and should not have to know which.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.domain.enums import DocumentFormat


class RendersPages(Protocol):
    """Anything that can hand back a document's pages."""

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[RenderedPage, ...]: ...


class PageRenderer:
    """Routes each format to the thing that can draw it, and draws each file once.

    A document is drawn for whoever asks -- the binding, the figure pass, the text
    grouping, the outline -- and drawing a slide deck means a browser. So the last file
    drawn is kept, keyed by what it was when it was drawn, and a file that changed under
    the same name is drawn again.
    ponytail: one document's pages held at a time, per-page streaming if a long
    document's renderings stop fitting in memory.
    """

    def __init__(self, *, browser: RendersPages, pdf: RendersPages | None = None) -> None:
        self._browser = browser
        self._pdf = pdf or PdfPageRenderer()
        self._drawn: tuple[tuple[Any, ...], tuple[RenderedPage, ...]] | None = None

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[RenderedPage, ...]:
        stat = source.stat()
        key = (str(source), document_format, stat.st_mtime_ns, stat.st_size)
        if self._drawn is not None and self._drawn[0] == key:
            return self._drawn[1]
        drawn = self._pdf if document_format is DocumentFormat.PDF else self._browser
        pages = tuple(drawn.render(source, document_format))
        self._drawn = (key, pages)
        return pages

"""Rasterise a PDF into the same pages the OOXML renderer produces.

A PDF is already laid out, so there is nothing to draw with a browser: pymupdf renders
each page directly. What comes back is shaped like a browser-rendered page so the binding
and the hierarchy pass cannot tell the two apart.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.application.xlsx_crop_regions import PixelBox
from graphme.domain.enums import DocumentFormat

RENDER_DPI = 144
"""Twice the 72 dpi a PDF states its own geometry in, so a point is two pixels."""
PREVIEW_DPI = 96
"""A page drawn for a reader to look at, not read."""


class PdfPageRenderer:
    """Every page of a PDF, drawn once."""

    def __init__(self, *, dpi: int = RENDER_DPI) -> None:
        self._dpi = dpi

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[RenderedPage, ...]:
        if document_format is not DocumentFormat.PDF:
            raise ValueError(f"{document_format} is not a PDF")
        pages: list[RenderedPage] = []
        with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
            for offset in range(document.page_count):
                page = document.load_page(offset)
                pixmap = page.get_pixmap(dpi=self._dpi, colorspace=pymupdf.csRGB, alpha=False)
                pages.append(
                    RenderedPage(
                        index=offset + 1,
                        width=pixmap.width,
                        height=pixmap.height,
                        png=pixmap.tobytes("png"),
                    )
                )
        return tuple(pages)

    def preview(self, source: Path, document_format: DocumentFormat, index: int) -> bytes:
        """Page `index` (from 1) alone, at PREVIEW_DPI."""
        if document_format is not DocumentFormat.PDF:
            raise ValueError(f"{document_format} is not a PDF")
        with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
            pixmap = document.load_page(index - 1).get_pixmap(
                dpi=PREVIEW_DPI, colorspace=pymupdf.csRGB, alpha=False)
            return bytes(pixmap.tobytes("png"))


INK_PAGE_SHARE = 0.1
"""How much of the page a vector path may cover and still be read as something drawn on
it. A report's layout is vector too -- the cell a column of text sits in, the band behind
a title -- and those cover a quarter of the page each."""
INK_PAGE_SPAN = 0.6
"""How much of a page's width or height a path may span. A rule drawn across the page
separates; it does not belong to whatever figure happens to touch it."""


def page_ink(source: Path, *, dpi: int = RENDER_DPI) -> dict[int, list[PixelBox]]:
    """Every vector path a PDF draws, in the pixels the page was rastered at.

    Frames, arrows and callouts are drawn, not placed: they never become nodes, so they
    are territory a figure's crop can take in rather than units of their own.
    """
    scale = dpi / 72.0
    found: dict[int, list[PixelBox]] = {}
    with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
        for offset in range(document.page_count):
            page = document.load_page(offset)
            width, height = page.rect.width, page.rect.height
            area = max(1.0, width * height)
            drawn: list[PixelBox] = []
            for path in page.get_drawings():
                rect = path["rect"] * page.rotation_matrix  # unrotated; the page is shown rotated
                if rect.width * rect.height > area * INK_PAGE_SHARE:
                    continue
                if rect.width > width * INK_PAGE_SPAN or rect.height > height * INK_PAGE_SPAN:
                    continue
                drawn.append(
                    PixelBox(
                        x0=rect.x0 * scale,
                        y0=rect.y0 * scale,
                        x1=rect.x1 * scale,
                        y1=rect.y1 * scale,
                    )
                )
            found[offset + 1] = drawn
    return found

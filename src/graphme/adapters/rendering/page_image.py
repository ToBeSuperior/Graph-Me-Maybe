from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import pymupdf

from graphme.adapters.rendering.browser_office import (
    BROWSER_FORMATS,
    BrowserRenderResult,
    RenderedPage,
)
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import SourceLocator
from graphme.ports.storage import AssetStore


@dataclass(frozen=True, slots=True)
class PageImage:
    page_index: int
    asset_hash: str
    mime_type: Literal["image/png"]
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class SourcePageMapping:
    locator: SourceLocator
    page_start: int
    page_end: int

    def __post_init__(self) -> None:
        if self.page_start < 1 or self.page_end < 1:
            raise ValueError("Rendered page spans must use positive page indexes")
        if self.page_start > self.page_end:
            raise ValueError("Rendered page spans must be ordered")


@dataclass(frozen=True, slots=True)
class PageImageRenderResult:
    pages: tuple[PageImage, ...]
    mappings: tuple[SourcePageMapping, ...]



class BrowserOfficeLike(Protocol):
    """The browser renderer this adapter delegates every OOXML format to."""

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[RenderedPage, ...]: ...

    def render_with_mapping(
        self, source: Path, document_format: DocumentFormat
    ) -> BrowserRenderResult: ...


def _format_of(source: Path) -> DocumentFormat:
    suffix = source.suffix.lower().lstrip(".")
    for document_format, kind in BROWSER_FORMATS.items():
        if kind == suffix:
            return document_format
    raise ValueError(f"not an OOXML source: {source}")


class PageImageRenderer:


    def __init__(
        self,
        *,
        asset_store: AssetStore,
        browser_renderer: BrowserOfficeLike | None = None,
    ) -> None:
        self._asset_store = asset_store
        # PDF is already page images; every OOXML format goes to the browser, which
        # renders a worksheet or slide whole instead of paginating it onto A4.
        self._browser_renderer = browser_renderer

    def render(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[PageImage, ...]:
        self._validate_source(source, document_format)
        try:
            if document_format is DocumentFormat.PDF:
                return self._render_pdf(source)
            return self._render_office(source)
        except (pymupdf.EmptyFileError, pymupdf.FileDataError) as exc:
            raise ValueError(f"Unable to render PDF: {source}") from exc

    def render_with_mapping(
        self, source: Path, document_format: DocumentFormat
    ) -> PageImageRenderResult:
        self._validate_source(source, document_format)
        try:
            if document_format is DocumentFormat.PDF:
                return self._render_pdf_with_mapping(source)
            return self._render_office_with_mapping(source, document_format)
        except (pymupdf.EmptyFileError, pymupdf.FileDataError) as exc:
            raise ValueError(f"Unable to render PDF: {source}") from exc

    def render_with_optional_mapping(
        self, source: Path, document_format: DocumentFormat
    ) -> tuple[PageImageRenderResult, str | None]:
        """Render pages even when source-to-page mapping cannot be established.

        A workbook whose sheets do not land on distinct pages still yields usable page
        images; the mapping failure is returned rather than raised so the caller can
        record it beside the render instead of losing both.
        """
        self._validate_source(source, document_format)
        try:
            return self.render_with_mapping(source, document_format), None
        except ValueError as exc:
            if isinstance(exc, (pymupdf.EmptyFileError, pymupdf.FileDataError)):
                raise
            mapping_error = str(exc)
        pages = (
            self._render_pdf(source)
            if document_format is DocumentFormat.PDF
            else self._render_office(source)
        )
        return PageImageRenderResult(pages=pages, mappings=()), mapping_error

    def _validate_source(
        self, source: Path, document_format: DocumentFormat
    ) -> None:
        if (
            document_format is not DocumentFormat.PDF
            and document_format not in BROWSER_FORMATS
        ):
            raise ValueError(
                f"Unsupported document format for page rendering: {document_format}"
            )
        if not source.is_file():
            raise FileNotFoundError(source)

    def _browser(self) -> BrowserOfficeLike:
        if self._browser_renderer is None:
            raise RuntimeError(
                "a browser renderer is required to render OOXML documents"
            )
        return self._browser_renderer

    def _render_office(self, source: Path) -> tuple[PageImage, ...]:
        return self._store_pages(self._browser().render(source, _format_of(source)))

    def _render_office_with_mapping(
        self, source: Path, document_format: DocumentFormat
    ) -> PageImageRenderResult:
        rendered: BrowserRenderResult = self._browser().render_with_mapping(
            source, document_format
        )
        return PageImageRenderResult(
            pages=self._store_pages(rendered.pages),
            mappings=tuple(
                SourcePageMapping(m.locator, m.page_start, m.page_end)
                for m in rendered.mappings
            ),
        )

    def _store_pages(self, pages: tuple[RenderedPage, ...]) -> tuple[PageImage, ...]:
        stored = []
        for page in pages:
            asset_hash = self._asset_store.put(page.png, suffix=".png")
            stored.append(
                PageImage(
                    page_index=page.index,
                    width=page.width,
                    height=page.height,
                    asset_hash=asset_hash,
                    mime_type="image/png",
                )
            )
        return tuple(stored)

    def _render_pdf(self, source: Path) -> tuple[PageImage, ...]:
        with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
            self._validate_pdf_has_pages(document, source)
            return self._rasterize_pdf(document)

    def _render_pdf_with_mapping(self, source: Path) -> PageImageRenderResult:
        with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
            self._validate_pdf_has_pages(document, source)
            mappings = self._map_document_pages(source, document.page_count)
            pages = self._rasterize_pdf(document)
        return PageImageRenderResult(pages=pages, mappings=mappings)

    @staticmethod
    def _validate_pdf_has_pages(document: pymupdf.Document, source: Path) -> None:
        if document.page_count == 0:
            raise ValueError(f"PDF has no pages: {source}")

    def _rasterize_pdf(
        self, document: pymupdf.Document
    ) -> tuple[PageImage, ...]:
        pages: list[PageImage] = []
        for page_offset in range(document.page_count):
            page = document.load_page(page_offset)  # type: ignore[no-untyped-call]
            pixmap = page.get_pixmap(
                dpi=144,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            png = pixmap.tobytes("png")
            asset_hash = self._asset_store.put(png, suffix=".png")
            pages.append(
                PageImage(
                    page_index=page_offset + 1,
                    asset_hash=asset_hash,
                    mime_type="image/png",
                    width=pixmap.width,
                    height=pixmap.height,
                )
            )
        return tuple(pages)

    @staticmethod
    def _map_document_pages(
        source: Path, page_count: int
    ) -> tuple[SourcePageMapping, ...]:
        return tuple(
            SourcePageMapping(
                locator=SourceLocator(
                    file_name=source.name,
                    label=source.name,
                    page=page_index,
                ),
                page_start=page_index,
                page_end=page_index,
            )
            for page_index in range(1, page_count + 1)
        )


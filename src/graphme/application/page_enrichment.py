"""Everything a document's pages are asked about after the outline is read, in one pass.

Captions and references are two questions about the same pages, and each pass that needs
the pages renders them: the browser draws a document once here and both questions are put
to what it drew. Rendering a long document twice cost more than either question does.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from graphme.application.document_references import link_references
from graphme.application.page_units import flow_pages
from graphme.application.visual_captions import caption_visuals
from graphme.domain.models import ExtractedDocument
from graphme.ports.storage import AssetStore
from graphme.ports.vision import VisionProvider


class PageEnricher(Protocol):
    """Adds what the pages say beyond their own words: captions, and documents named."""

    def enrich(
        self,
        *,
        extracted: ExtractedDocument,
        source_path: str,
        document_format: Any,
        on_phase: Callable[[str], None] | None = None,
    ) -> ExtractedDocument: ...


class RenderedPageEnricher:
    """Renders a document once and asks its pages both questions."""

    def __init__(
        self,
        *,
        renderer: Any,
        vision: VisionProvider,
        asset_store: AssetStore,
        workers: int = 1,
    ) -> None:
        self._renderer = renderer
        self._vision = vision
        self._asset_store = asset_store
        self._workers = workers
        """How many pages are asked at once; each page is its own question here."""

    def enrich(
        self,
        *,
        extracted: ExtractedDocument,
        source_path: str,
        document_format: Any,
        on_phase: Callable[[str], None] | None = None,
    ) -> ExtractedDocument:
        """Captions, then the documents the pages name. `on_phase` hears which is running."""
        told = on_phase or (lambda phase: None)
        pages = self._renderer.render(Path(source_path), document_format)
        page_of = flow_pages(extracted, pages, document_format)
        told("captioning")
        enriched = caption_visuals(
            extracted,
            pages=pages,
            vision=self._vision,
            asset_store=self._asset_store,
            page_of=page_of,
            workers=self._workers,
        )
        told("referencing")
        return link_references(
            enriched,
            pages=pages,
            vision=self._vision,
            asset_store=self._asset_store,
            page_of=page_of,
            workers=self._workers,
        )

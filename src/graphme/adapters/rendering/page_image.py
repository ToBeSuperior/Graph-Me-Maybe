from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from graphme.adapters.rendering.browser_office import BrowserRenderResult, SourcePageMapping
from graphme.ports.storage import AssetStore


@dataclass(frozen=True, slots=True)
class PageImage:
    page_index: int
    asset_hash: str
    mime_type: Literal["image/png"]
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PageImageRenderResult:
    pages: tuple[PageImage, ...]
    mappings: tuple[SourcePageMapping, ...]


def store_pages(rendered: BrowserRenderResult, asset_store: AssetStore) -> PageImageRenderResult:
    """The pages the browser drew, kept in `asset_store` by content, with where each came from."""
    pages = tuple(
        PageImage(
            page_index=page.index,
            asset_hash=asset_store.put(page.png, suffix=".png"),
            mime_type="image/png",
            width=page.width,
            height=page.height,
        )
        for page in rendered.pages
    )
    return PageImageRenderResult(pages=pages, mappings=rendered.mappings)

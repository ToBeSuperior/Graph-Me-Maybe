"""Bind a document's rendered crops onto its graph, one page at a time.

Extraction runs without ever drawing the document, so a table node has no picture of
itself and a photograph sitting in a results table is parented by position rather than by
the table that encloses it. Rendering supplies both.

A worksheet, a slide and a page of flowing text differ only in where a box comes from --
cells looked up through the edges the renderer recorded, the rectangle a shape carries, or
the place the layout happened to put a paragraph. The rules on top of the box are the same
for all three.
"""
from __future__ import annotations

import functools
import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from graphme.adapters.extractors.xlsx_drawings import DrawingObject, parse_xlsx_drawings
from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import RendersPages
from graphme.adapters.rendering.pdf_pages import page_ink
from graphme.application.figure_groups import widen_figures
from graphme.application.page_units import flow_pages
from graphme.application.unit_placement import (
    CellRangePlacer,
    FlowPlacer,
    NormalizedBoxPlacer,
    UnitPlacer,
    home_pages,
)
from graphme.application.visual_crops import bind_page_visuals, crop_box
from graphme.domain.enums import DocumentFormat, NodeType
from graphme.domain.models import ExtractedDocument, GraphNode, HierarchyFigureGroup
from graphme.ports.storage import AssetStore

BINDABLE_FORMATS = (
    DocumentFormat.XLSX,
    DocumentFormat.PPTX,
    DocumentFormat.DOCX,
    DocumentFormat.PDF,
)
"""Formats the browser renders, whichever way they state where a thing sits."""


class RenderedVisualBinder:
    """Renders a document once and binds every page's visuals onto the graph."""

    def __init__(
        self, *, renderer: RendersPages, asset_store: AssetStore
    ) -> None:
        self._renderer = renderer
        self._asset_store = asset_store

    def drawn(self, *, extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
        """The graph with where each node is drawn written on it (`with_geometry`)."""
        fmt = extracted.document.format
        if fmt not in BINDABLE_FORMATS:
            return extracted
        return with_geometry(extracted, self._renderer.render(source, fmt), source)

    def widen(
        self,
        *,
        extracted: ExtractedDocument,
        source: Path,
        figures: Sequence[HierarchyFigureGroup],
    ) -> ExtractedDocument:
        """Cut each figure's crop again around the parts the page says are drawn with it.

        Territory is measured before anything is read, and a drawing's labels lie outside
        the picture they name. The hierarchy pass says which ones belong to it; this is
        where that answer reaches the crop.
        """
        fmt = extracted.document.format
        if not figures or fmt not in BINDABLE_FORMATS:
            return extracted
        drawings = parse_xlsx_drawings(source).objects if fmt is DocumentFormat.XLSX else ()
        pages = self._renderer.render(source, fmt)
        home = home_pages_of(extracted, pages, fmt)
        placers: dict[int, UnitPlacer] = {}
        page_of: dict[str, int] = {}
        png_of = {page.index: page.png for page in pages}
        for page in pages:
            placed = page_members(extracted, page, fmt, drawings, home)
            if placed is None:
                continue
            members, placer = placed
            placers[page.index] = placer
            for node in members:
                page_of.setdefault(node.id, page.index)

        # One page decoded at a time: every page of a long document at once was hundreds of
        # megabytes, held for figures on a few of them.
        @functools.lru_cache(maxsize=1)
        def decoded(index: int) -> Image.Image:
            with Image.open(io.BytesIO(png_of[index])) as opened:
                return opened.convert("RGB")

        def crop(index: int, box: tuple[int, int, int, int]) -> bytes:
            buffer = io.BytesIO()
            decoded(index).crop(box).save(buffer, format="PNG")
            return buffer.getvalue()

        return widen_figures(
            extracted,
            figures=figures,
            placers=placers,
            page_of=page_of,
            crop=crop,
            asset_store=self._asset_store,
        )

    def bind(self, *, extracted: ExtractedDocument, source: Path) -> ExtractedDocument:
        fmt = extracted.document.format
        if fmt not in BINDABLE_FORMATS:
            return extracted
        # A worksheet picture is cropped to its own extent, which only the anchors carry.
        drawings = parse_xlsx_drawings(source).objects if fmt is DocumentFormat.XLSX else ()
        pages = self._renderer.render(source, fmt)
        # A PDF draws a diagram's frame and arrows as paths; they are no unit's box but
        # they are part of the picture a figure's crop should show.
        ink = page_ink(source) if fmt is DocumentFormat.PDF else {}
        # Flowing text crosses page breaks, and a table can be drawn on two pages. Each
        # node is bound once, where the page shows most of it.
        home = home_pages_of(extracted, pages, fmt)
        for page in pages:
            placed = page_members(extracted, page, fmt, drawings, home)
            if placed is None:
                continue
            members, placer = placed
            with Image.open(io.BytesIO(page.png)) as opened:
                page_image = opened.convert("RGB")

            def crop(box: tuple[int, int, int, int], image: Image.Image = page_image) -> bytes:
                buffer = io.BytesIO()
                image.crop(box).save(buffer, format="PNG")
                return buffer.getvalue()

            extracted = bind_page_visuals(
                extracted,
                page=page,
                members=members,
                placer=placer,
                asset_store=self._asset_store,
                crop=crop,
                ink=ink.get(page.index, ()),
            )
        return extracted


def home_pages_of(
    extracted: ExtractedDocument,
    pages: Sequence[RenderedPage],
    fmt: DocumentFormat,
) -> dict[str, int] | None:
    """Flowing text crosses page breaks: each node is bound where most of it is."""
    if fmt is not DocumentFormat.DOCX:
        return None
    return home_pages(
        extracted.nodes,
        {page.index: FlowPlacer.of(page) for page in pages},
    )


def page_members(
    extracted: ExtractedDocument,
    page: RenderedPage,
    fmt: DocumentFormat,
    drawings: Sequence[DrawingObject],
    home: dict[str, int] | None = None,
) -> tuple[list[GraphNode], UnitPlacer] | None:
    if fmt is DocumentFormat.DOCX:
        flow: UnitPlacer = FlowPlacer.of(page)
        members = [
            node
            for node in extracted.nodes
            if (home or {}).get(node.id) == page.index and flow.box(node) is not None
        ]
        return (members, flow) if members else None
    if fmt is DocumentFormat.XLSX:
        grid = page.grid()
        label = page.label
        if grid is None or label is None:
            return None
        placer = CellRangePlacer(
            grid,
            extents={
                item.xml_index: item
                for item in drawings
                if item.worksheet == label
            },
        )
        return [n for n in extracted.nodes if n.locator.sheet == label], placer
    members = [
        node
        for node in extracted.nodes
        if (node.locator.page if fmt is DocumentFormat.PDF else node.locator.slide)
        == page.index
    ]
    if not members:
        return None
    return members, NormalizedBoxPlacer(
        width=page.width, height=page.height, glyphs=page.glyph_boxes()
    )


def placed_units(
    extracted: ExtractedDocument, pages: Sequence[RenderedPage], source: Path
) -> dict[str, tuple[int, tuple[int, int, int, int]]]:
    """Where each node is drawn: its page and its pixel box there, placed the way binding
    places it -- a node crossing a page break on the page that shows most of it. What a
    reader is shown when a passage is pointed at on the page."""
    fmt = extracted.document.format
    if fmt not in BINDABLE_FORMATS:
        return {}
    drawings = parse_xlsx_drawings(source).objects if fmt is DocumentFormat.XLSX else ()
    home = home_pages_of(extracted, pages, fmt)
    placed: dict[str, tuple[int, tuple[int, int, int, int]]] = {}
    for page in pages:
        found = page_members(extracted, page, fmt, drawings, home)
        if found is None:
            continue
        members, placer = found
        for node in members:
            # A visual is shown by the box its stored crop was cut to -- the one producer of
            # a crop's box, which takes in the ink and the parts a re-measure would miss.
            box = crop_box(node) or placer.box(node)
            if box is not None and node.id not in placed:
                placed[node.id] = (page.index, box.rounded())
    return placed


def page_name(page: RenderedPage, fmt: DocumentFormat) -> str | None:
    """A sheet's own name. Any other page the page names by its number, in its own language."""
    return page.label if fmt is DocumentFormat.XLSX and page.label else None


def pages_of(
    extracted: ExtractedDocument, pages: Sequence[RenderedPage], fmt: DocumentFormat
) -> dict[str, int]:
    """The page each node is drawn on -- a worksheet names it by sheet, a slide by number."""
    if fmt is DocumentFormat.DOCX:
        return dict(flow_pages(extracted, pages, fmt))
    if fmt is DocumentFormat.XLSX:
        by_sheet = {page.label: page.index for page in pages}
        return {
            n.id: by_sheet[n.locator.sheet] for n in extracted.nodes if n.locator.sheet in by_sheet
        }
    numbers = {page.index for page in pages}
    found = {
        n.id: n.locator.page if fmt is DocumentFormat.PDF else n.locator.slide
        for n in extracted.nodes
    }
    return {node: number for node, number in found.items() if number is not None
            and number in numbers}


def with_geometry(
    extracted: ExtractedDocument, pages: Sequence[RenderedPage], source: Path
) -> ExtractedDocument:
    """Where the document is drawn, written on its graph: each node's page and its box there
    (`page`, `box`), and the pages themselves on the document node (`pages`: index, name,
    width, height). A preview shows a document by these and draws only the page looked at;
    no page image is kept."""
    fmt = extracted.document.format
    if fmt not in BINDABLE_FORMATS or not pages:
        return extracted
    page_of = pages_of(extracted, pages, fmt)
    placed = placed_units(extracted, pages, source)
    drawn = [{"index": page.index, "name": page_name(page, fmt), "width": page.width,
              "height": page.height} for page in pages]
    nodes = []
    for node in extracted.nodes:
        metadata = dict(node.metadata)
        if node.type is NodeType.DOCUMENT:
            metadata["pages"] = drawn  # type: ignore[assignment]
        if node.id in page_of:
            metadata["page"] = page_of[node.id]
        if node.id in placed:
            metadata["page"], metadata["box"] = placed[node.id][0], list(placed[node.id][1])
        nodes.append(node if metadata == node.metadata
                     else node.model_copy(update={"metadata": metadata}))
    return extracted.model_copy(update={"nodes": tuple(nodes)})

"""Derive a document's outline by showing each rendered page to a vision model.

Wired in only when the document asks for a derived outline. What the extraction read
off the file is sent -- unit text, cell ranges turned into pixels, whether a thing is a
table, a picture or text -- and what it judged is withheld, because a judged heading or
a parent link hands the model the answer it is being asked for.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from graphme.adapters.extractors.xlsx_drawings import DrawingObject, parse_xlsx_drawings
from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import RendersPages
from graphme.adapters.vision.carried_outline import Carried, Placements
from graphme.application.outline_join import (
    JOIN_READINGS,
    MAX_HEADING_LINES,
    Answer,
    Joining,
    PageTree,
    headings,
)
from graphme.application.page_hierarchy_scope import (
    HierarchyScopeUnits,
    build_placed_scope,
    build_sheet_scope,
)
from graphme.application.page_units import flow_pages
from graphme.application.unit_placement import (
    FlowPlacer,
    NormalizedBoxPlacer,
    UnitPlacer,
)
from graphme.application.visual_crops import read_node_crop
from graphme.domain.enums import FLOW_FORMATS, DocumentFormat, NodeType
from graphme.domain.models import (
    ExtractedDocument,
    GraphNode,
    HeadingLine,
    HierarchyDecision,
    HierarchyDerivation,
    HierarchyFigureGroup,
    HierarchyImageDescriptor,
    HierarchyScopeRequest,
    HierarchyScopeResult,
    HierarchyUnit,
    JoinPlacement,
    PageJoinRequest,
)
from graphme.ports.storage import AssetStore
from graphme.ports.vision import VisionProvider

_log = logging.getLogger(__name__)

PICTURE_TYPES = {NodeType.IMAGE, NodeType.CHART}
"""Units whose label cannot identify them. A table's label carries its header row and
cell range; a picture's is "그림 5", so its crop is shown alongside."""


def unit_crops(
    units: Iterable[HierarchyUnit],
    *,
    nodes: Mapping[str, GraphNode],
    asset_store: AssetStore,
) -> dict[str, bytes]:
    """The stored crop of every picture in the scope, keyed by the unit's id."""
    crops: dict[str, bytes] = {}
    for unit in units:
        if unit.node_type not in PICTURE_TYPES:
            continue
        node = nodes.get(unit.id)
        crop = None if node is None else read_node_crop(node, asset_store=asset_store)
        if crop is not None:
            crops[unit.id] = crop
    return crops


MAX_SCOPE_UNITS = 500


SCOPE_FORMATS = (
    DocumentFormat.XLSX,
    DocumentFormat.PPTX,
    DocumentFormat.DOCX,
    DocumentFormat.PDF,
)
"""Formats the browser renders. A worksheet is placed through the cell edges the renderer
recorded, a slide through the rectangle each shape carries, and a page of flowing text
through where the layout drew each paragraph, and a PDF page through the rectangle the
file states for each block; the question is the same for all four."""

TAIL_FRACTION = 0.3
"""How much of the page before a continuation page is shown with it. The end of a page is
what the next one carries on from; the whole page would cost a second page of tokens to
say the same thing."""

class RenderedHierarchyScopeProvider:
    """Runs one scope pass per rendered page."""

    def __init__(
        self,
        *,
        renderer: RendersPages,
        vision: VisionProvider,
        asset_store: AssetStore | None = None,
        readings: int = 1,
        limiter: AbstractContextManager[Any] | None = None,
        workers: int = 1,
    ) -> None:
        if readings < 1:
            raise ValueError("a scope needs at least one reading")
        self._workers = max(1, workers)
        """How many pages, and page breaks, are asked at once."""
        self._limiter = limiter if limiter is not None else nullcontext()
        """Held around every model call, so a caller reading several documents at once caps
        the calls in flight across all of them."""
        self._renderer = renderer
        self._vision = vision
        self._asset_store = asset_store
        self._readings = readings
        """How many independent readings of each page to take. More than one is put to
        the page: the readings run together, and where they differ the model is shown
        both beside the rendering and says which one it keeps."""

    def _pages(self, source_path: str, document_format: DocumentFormat) -> tuple[RenderedPage, ...]:
        """This document's rendered pages. Drawing them once across the passes that ask is
        the renderer's business, not this pass's."""
        return tuple(self._renderer.render(Path(source_path), document_format))

    @staticmethod
    def _drawings(source_path: str, document_format: DocumentFormat) -> tuple[DrawingObject, ...]:
        """A workbook's pictures and charts as drawn: their own extent, not their anchor cells."""
        if document_format is not DocumentFormat.XLSX:
            return ()
        return tuple(parse_xlsx_drawings(Path(source_path)).objects)

    def group_figures(
        self, *, extracted: ExtractedDocument, source_path: str
    ) -> tuple[HierarchyFigureGroup, ...]:
        """Which units each page draws as one figure -- asked before any text is grouped.

        A figure swallows the words written on it, and what it swallows is not a paragraph
        to be joined to the paragraph below. So this comes first, on its own: no outline
        travels between pages, because nothing is being placed under anything yet.
        """
        document_format = extracted.document.format
        if document_format not in SCOPE_FORMATS:
            return ()
        nodes = {node.id: node for node in extracted.nodes}
        pages = self._pages(source_path, document_format)
        home = flow_pages(extracted, pages, document_format)
        drawings = self._drawings(source_path, document_format)

        def read(page: RenderedPage) -> tuple[HierarchyFigureGroup, ...]:
            asked = self._asked(extracted, page, home, nodes, drawings)
            if asked is None:
                return ()
            scope, shown, crops = asked
            request = self._request(extracted, scope, shown)
            try:
                with self._limiter:
                    answered = self._vision.group_page_figures(
                        request=request, image=shown.png, unit_images=crops
                    )
            except RuntimeError as error:
                # A page whose figures cannot be read keeps the territory measurement gave
                # it. Nothing is grouped there, and the rest of the document still is.
                _log.warning("%s: no usable figure answer -- %s", scope.scope, error)
                return ()
            return answered.figures

        # Nothing passes between pages here, so they are asked `workers` at a time.
        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(pages)))) as pool:
            return tuple(group for found in pool.map(read, pages) for group in found)

    def derive(
        self,
        *,
        extracted: ExtractedDocument,
        source_path: str,
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None = None,
    ) -> HierarchyDerivation:
        """Read every page's outline. Pages that stand alone -- sheets, slides -- are each
        their own tree. A flowing document's pages are read side by side, each on its own
        against the document's headings, and then every page break is joined
        (`outline_join`): reading them in order, each page waiting for the one before, was
        the slowest part of a build and no more right.

        `on_page` hears each page as its reading lands -- its decisions, or None when the
        page got no usable answer -- so a long document shows what is read while the rest is
        still being read. What the joins decide comes back in the derivation.
        """
        document_format = extracted.document.format
        if document_format not in SCOPE_FORMATS:
            return HierarchyDerivation()  # nothing else is rendered one page per authored unit
        nodes = {node.id: node for node in extracted.nodes}
        pages = self._pages(source_path, document_format)
        home = flow_pages(extracted, pages, document_format)
        drawings = self._drawings(source_path, document_format)
        flowing = document_format in FLOW_FORMATS
        if not flowing:
            return self._derive_apart(extracted, pages, home, nodes, drawings, on_page)
        asked = {page.index: self._asked(extracted, page, home, nodes, drawings)
                 for page in pages}
        scopes = [(index, found[0]) for index, found in asked.items() if found is not None]
        units = {unit.id: unit for _, scope in scopes for unit in scope.units}
        order = {unit.id: (index, place) for index, scope in scopes
                 for place, unit in enumerate(scope.units)}
        heads = self._headings(scopes)
        read = self._read_pages(extracted, pages, asked, heads, units, order, on_page)
        root = next(node for node in extracted.nodes if node.type is NodeType.DOCUMENT)
        joining = Joining(
            root=root.id,
            root_label=extracted.document.file_name or root.label,
            trees=[PageTree.of(index, [unit.id for unit in scope.units], read[index] or ())
                   for index, scope in scopes if read.get(index) is not None],
            units=units,
            order=order,
            heads=heads,
        )
        requests = [request for index, _ in scopes
                    if (request := joining.request(index)) is not None]
        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(requests)))) as pool:
            joined = dict(zip((r.page_index for r in requests),
                              pool.map(lambda r: self._join(joining, r), requests),
                              strict=True))
        return HierarchyDerivation(
            decisions=joining.settle(joined),
            unread_pages=tuple(index for index, _ in scopes if read.get(index) is None),
        )

    def _headings(
        self, scopes: Sequence[tuple[int, HierarchyScopeUnits]]
    ) -> tuple[HierarchyDecision, ...]:
        """The document's headings, read off its text, or none: past `MAX_HEADING_LINES`, or
        when the model gives no usable answer, the pages are read and joined without them."""
        lines = [HeadingLine(unit_id=unit.id, page=index, indent=unit.indent or 0,
                             label=unit.label)
                 for index, scope in scopes for unit in scope.units
                 if unit.node_type is NodeType.TEXT_CHUNK]
        if not lines or len(lines) > MAX_HEADING_LINES:
            return ()
        try:
            with self._limiter:
                answered = self._vision.outline_headings(lines=lines)
        except RuntimeError as error:
            _log.warning("no usable headings, joined without them -- %s", error)
            return ()
        return headings(lines, answered)

    def _read_pages(
        self,
        extracted: ExtractedDocument,
        pages: Sequence[RenderedPage],
        asked: Mapping[int, tuple[HierarchyScopeUnits, RenderedPage, dict[str, bytes] | None]
                       | None],
        heads: Sequence[HierarchyDecision],
        units: Mapping[str, HierarchyUnit],
        order: Mapping[str, tuple[int, int]],
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None,
    ) -> dict[int, tuple[HierarchyDecision, ...] | None]:
        """Every page read on its own, side by side, shown the headings before it and the
        foot of the page before. Each is heard as it lands; None is a page left unread."""
        ordered = sorted((d for d in heads if d.subject_id in units),
                         key=lambda d: order[d.subject_id])

        def read(page: RenderedPage) -> tuple[int, tuple[HierarchyDecision, ...] | None]:
            found = asked[page.index]
            assert found is not None
            scope, shown, crops = found
            before = [d for d in ordered if order[d.subject_id][0] < page.index]
            carried = Carried()
            if before:
                placements = Placements()
                placements.record(before)
                seen = {d.subject_id: units[d.subject_id] for d in before}
                carried = placements.carried(replace(scope, units=tuple(seen.values())), seen)
            previous = asked.get(page.index - 1)
            try:
                answered = self._read(self._request(extracted, scope, shown, carried),
                                      shown.png, crops,
                                      _page_tail(previous[1].png) if previous else None)
            except RuntimeError as error:
                # One page the model cannot answer is one page's outline, not the document's.
                _log.warning("%s: no usable answer, left as it was -- %s", scope.scope, error)
                result: tuple[int, tuple[HierarchyDecision, ...] | None] = (page.index, None)
            else:
                result = (page.index, answered.decisions)
            if on_page is not None:
                on_page(*result)
            return result

        todo = [page for page in pages if asked.get(page.index) is not None]
        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(todo)))) as pool:
            return dict(pool.map(read, todo))

    def _join(self, joining: Joining, request: PageJoinRequest) -> dict[str, Answer]:
        """One page break: `JOIN_READINGS` readings, a third where they differ, the vote."""
        def once() -> tuple[JoinPlacement, ...]:
            try:
                with self._limiter:
                    return self._vision.join_page(request=request)
            except RuntimeError as error:
                _log.warning("page %d: no usable join -- %s", request.page_index, error)
                return ()

        readings = [once() for _ in range(JOIN_READINGS)]
        if joining.disagree(request, readings):
            readings.append(once())
        return joining.vote(request, readings)

    def _derive_apart(
        self,
        extracted: ExtractedDocument,
        pages: Sequence[RenderedPage],
        home: Mapping[str, int],
        nodes: Mapping[str, GraphNode],
        drawings: Sequence[DrawingObject],
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None,
    ) -> HierarchyDerivation:
        """Pages that stand alone, read `workers` at a time and each heard as it lands."""

        def read(page: RenderedPage) -> tuple[int, tuple[HierarchyDecision, ...] | None]:
            asked = self._asked(extracted, page, home, nodes, drawings)
            if asked is None:
                return page.index, ()
            scope, shown, crops = asked
            try:
                answered = self._read(self._request(extracted, scope, shown), shown.png, crops)
            except RuntimeError as error:
                _log.warning("%s: no usable answer, left as it was -- %s", scope.scope, error)
                result: tuple[int, tuple[HierarchyDecision, ...] | None] = (page.index, None)
            else:
                result = (page.index, answered.decisions)
            if on_page is not None and (asked is not None):
                on_page(*result)
            return result

        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(pages)))) as pool:
            results = list(pool.map(read, pages))
        return HierarchyDerivation(
            decisions=tuple(d for _, found in results for d in (found or ())),
            unread_pages=tuple(index for index, found in results if found is None),
        )

    def _asked(
        self,
        extracted: ExtractedDocument,
        page: RenderedPage,
        home: Mapping[str, int],
        nodes: Mapping[str, GraphNode],
        drawings: Sequence[DrawingObject] = (),
    ) -> tuple[HierarchyScopeUnits, RenderedPage, dict[str, bytes] | None] | None:
        """What one page is asked with: its units, the page shown, and each picture's crop.

        The margins of a page carry nothing, so what goes out is the page cut back to what
        is drawn on it; the crops are cut from the page itself and are not touched by this,
        so no box moves.
        """
        scope = self._scope(extracted, page, extracted.document.format, home, drawings)
        if scope is None or len(scope.units) > MAX_SCOPE_UNITS:
            return None
        crops = (
            unit_crops(scope.units, nodes=nodes, asset_store=self._asset_store)
            if self._asset_store is not None
            else None
        )
        return scope, _trimmed(page), crops

    def _read(
        self,
        request: HierarchyScopeRequest,
        image: bytes,
        crops: Mapping[str, bytes] | None,
        preceding: bytes | None = None,
    ) -> HierarchyScopeResult:
        """One page's outline, read as many times as asked and settled against the page."""
        def once() -> HierarchyScopeResult:
            with self._limiter:
                return self._vision.resolve_hierarchy_scope(
                    request=request, image=image, unit_images=crops, preceding=preceding
                )

        if self._readings == 1:
            return once()
        with ThreadPoolExecutor(max_workers=self._readings) as pool:
            readings = tuple(task.result() for task in [
                pool.submit(once) for _ in range(self._readings)
            ])
        # Agreement settles itself. Only where the readings differ is there a question,
        # and only then is it worth another call.
        if len({_stated(reading) for reading in readings}) == 1:
            _log.info("%s: %d readings agreed", request.scope, len(readings))
            return readings[0]
        with self._limiter:
            choice = self._vision.choose_hierarchy_scope(
                request=request, image=image, candidates=readings, unit_images=crops
            )
        if not 1 <= choice.chosen <= len(readings):
            raise ValueError("the settled reading is not one of the readings sent")
        _log.info(
            "%s: readings differed, kept #%d -- %s",
            request.scope,
            choice.chosen,
            choice.rationale,
        )
        return readings[choice.chosen - 1]

    @staticmethod
    def _scope(
        extracted: ExtractedDocument,
        page: RenderedPage,
        document_format: DocumentFormat,
        home: Mapping[str, int],
        drawings: Sequence[DrawingObject] = (),
    ) -> HierarchyScopeUnits | None:
        """One page's units, placed the way that format states a position."""
        if document_format in FLOW_FORMATS:
            root = next(
                (node for node in extracted.nodes if node.type is NodeType.DOCUMENT), None
            )
            if root is None:
                return None
            return build_placed_scope(
                extracted=extracted,
                members=_flow_members(extracted, page, document_format, home),
                placer=flow_placer(page, document_format),
                root=root,
                scope=f"page-{page.index:02d}",
                page_index=max(1, page.index),
                # Running text says nesting with indentation, and here the box is where
                # the words actually are rather than the cell they sit in.
                indented=True,
                page_width=float(page.width),
            )
        if document_format is DocumentFormat.PPTX:
            members = [
                node for node in extracted.nodes if node.locator.slide == page.index
            ]
            root = next(
                (node for node in members if node.type is NodeType.SLIDE), None
            )
            if root is None:
                return None
            return build_placed_scope(
                extracted=extracted,
                members=members,
                placer=NormalizedBoxPlacer(
                    width=page.width, height=page.height, glyphs=page.glyph_boxes()
                ),
                root=root,
                scope=f"slide-{page.index:02d}",
                page_index=page.index,
            )
        grid = page.grid()
        if grid is None or page.label is None:
            return None
        return build_sheet_scope(
            extracted=extracted,
            sheet_name=page.label,
            scope=f"sheet-{page.index:02d}",
            page_index=page.index,
            grid=grid,
            extents={item.xml_index: item for item in drawings if item.worksheet == page.label},
        )

    @staticmethod
    def _request(
        extracted: ExtractedDocument,
        scope: HierarchyScopeUnits,
        page: RenderedPage,
        carried: Carried | None = None,
    ) -> HierarchyScopeRequest:
        carried = carried or Carried()
        unit_ids = tuple(unit.id for unit in scope.units)
        return HierarchyScopeRequest(
            request_id=f"{extracted.document.id}:{scope.scope}",
            document_id=extracted.document.id,
            scope=scope.scope,
            page_index=scope.page_index,
            scope_root_id=scope.scope_root_id,
            units=scope.units,
            outline=carried.units,
            outline_parents=carried.parents,
            outline_open=carried.open,
            outline_omitted=carried.omitted,
            image=HierarchyImageDescriptor(
                asset_hash=hashlib.sha256(page.png).hexdigest(),
                size_bytes=len(page.png),
                width=page.width,
                height=page.height,
                document_id=extracted.document.id,
                scope=scope.scope,
                page_index=scope.page_index,
                covered_unit_ids=unit_ids,
            ),
        )


def _stated(result: HierarchyScopeResult) -> frozenset[tuple[str, str, str | None]]:
    """What a reading decided about every unit.

    Confidence and rationale are how a reading explains itself, not what it decided, so
    two readings that place everything the same way are the same reading here.
    """
    return frozenset(
        (decision.subject_id, decision.status, decision.parent_id)
        for decision in result.decisions
    )


def _page_tail(png: bytes) -> bytes | None:
    """The bottom of a page, which is what the next page carries on from."""
    try:
        with Image.open(BytesIO(png)) as opened:
            page = opened.convert("RGB")
            top = int(page.height * (1.0 - TAIL_FRACTION))
            return _png(page.crop((0, top, page.width, page.height)))
    except (OSError, ValueError):
        return None  # a page that cannot be cut is not a reason to lose the pass


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


TRIM_MARGIN = 0.05
"""How much blank to leave around what a page has on it. A rendered page is mostly margin
-- paper, not content -- and every pixel of it is sent, encoded, and read."""


def _trimmed(page: RenderedPage) -> RenderedPage:
    """The page with its blank margins cut back to a thin border.

    Only the picture changes. Crops are cut from the untrimmed page against boxes measured
    on it, so nothing that names a position moves.
    """
    try:
        with Image.open(BytesIO(page.png)) as opened:
            drawn = opened.convert("RGB")
            ink = drawn.convert("L").point(lambda value: 255 if value < 250 else 0)
            box = ink.getbbox()
            if box is None:
                return page
            left, top, right, bottom = box
            pad_x = max(1, int((right - left) * TRIM_MARGIN))
            pad_y = max(1, int((bottom - top) * TRIM_MARGIN))
            cut = drawn.crop((
                max(0, left - pad_x),
                max(0, top - pad_y),
                min(drawn.width, right + pad_x),
                min(drawn.height, bottom + pad_y),
            ))
            return replace(page, png=_png(cut), width=cut.width, height=cut.height)
    except (OSError, ValueError):
        return page  # a page that cannot be cut goes as it is


def flow_placer(page: RenderedPage, document_format: DocumentFormat) -> UnitPlacer:
    """Where this page's units sit: a PDF states it, a DOCX has to be measured."""
    if document_format is DocumentFormat.PDF:
        return NormalizedBoxPlacer(width=page.width, height=page.height)
    return FlowPlacer.of(page)


def _flow_members(
    extracted: ExtractedDocument,
    page: RenderedPage,
    document_format: DocumentFormat,
    home: Mapping[str, int],
) -> list[GraphNode]:
    """The nodes this page decides. A PDF block names its page; flowing text does not."""
    if document_format is DocumentFormat.PDF:
        return [node for node in extracted.nodes if node.locator.page == page.index]
    return [node for node in extracted.nodes if home.get(node.id) == page.index]

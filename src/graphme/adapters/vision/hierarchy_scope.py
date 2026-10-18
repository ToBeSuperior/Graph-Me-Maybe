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
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import RendersPages
from graphme.application.hierarchy_decisions import dropped_subjects
from graphme.application.page_hierarchy_scope import (
    HierarchyScopeUnits,
    build_placed_scope,
    build_sheet_scope,
)
from graphme.application.page_units import flow_pages
from graphme.application.text_match import estimated_tokens
from graphme.application.unit_placement import (
    FlowPlacer,
    NormalizedBoxPlacer,
    UnitPlacer,
)
from graphme.application.xlsx_visual_binding import VISUAL_TYPES, read_node_crop
from graphme.domain.enums import DocumentFormat, NodeType
from graphme.domain.ids import canonical_json
from graphme.domain.models import (
    ExtractedDocument,
    GraphNode,
    HierarchyDecision,
    HierarchyDerivation,
    HierarchyFigureGroup,
    HierarchyImageDescriptor,
    HierarchyScopeRequest,
    HierarchyScopeResult,
    HierarchyUnit,
)
from graphme.ports.vision import VisionProvider

_log = logging.getLogger(__name__)

RENDERER_VERSION = "ooxml-browser-1"
PICTURE_TYPES = {NodeType.IMAGE, NodeType.CHART}
"""Units whose label cannot identify them. A table's label carries its header row and
cell range; a picture's is "그림 5", so its crop is shown alongside."""


def unit_crops(
    units: Iterable[HierarchyUnit],
    *,
    nodes: Mapping[str, GraphNode],
    asset_store: Any,
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

FLOW_FORMATS = (DocumentFormat.DOCX, DocumentFormat.PDF)
"""Formats whose pages are where the paper ran out rather than a unit the author worked
in. Their pages are read in order and each carries forward what it left open."""

TAIL_FRACTION = 0.3
"""How much of the page before a continuation page is shown with it. The end of a page is
what the next one carries on from; the whole page would cost a second page of tokens to
say the same thing."""

CARRY_TOKEN_BUDGET = 1200
"""How much of the outline built so far travels to the next page. It is a reference the
page reads its own units against, so it is worth twice a search unit: too little and the
page sees a section it cannot recognise, too much and the model reaches past what the
page can settle."""

CARRY_LABEL_CHARS = 80
"""A carried heading is there to be recognised, not read. Its opening words do that."""

CARRY_ITEM_CHARS = 40
"""A carried unit holding nothing -- a paragraph, a list item -- is there to show what kind
of thing reads under its heading, and fewer words than a heading's do that."""

SKELETON_SHARE = 0.5
"""How much of the budget the document's headings may take, shallowest first. The rest
goes to whole recent sections. A long document's headings alone can outgrow the budget,
so its deepest levels are the first left out and its chapters the last."""

ENTRY_TOKENS = 8
"""What one carried line costs beyond its label: its id, its parent's id, the braces."""

CHAIN_DEPTH = 24
"""How far up the ancestry is walked to find the open chain. A guard against a cycle, not
a budget -- what fits is decided by tokens."""


class RenderedHierarchyScopeProvider:
    """Runs one scope pass per rendered page."""

    def __init__(
        self,
        *,
        renderer: RendersPages,
        vision: VisionProvider,
        asset_store: Any = None,
        readings: int = 1,
        limiter: AbstractContextManager[Any] | None = None,
        workers: int = 1,
    ) -> None:
        if readings < 1:
            raise ValueError("a scope needs at least one reading")
        self._workers = max(1, workers)
        """How many pages of a document whose pages stand alone -- sheets, slides -- are read
        at once. A flowing document's page waits for the one before; those never do."""
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
        figures: list[HierarchyFigureGroup] = []
        pages = self._pages(source_path, document_format)
        home = flow_pages(extracted, pages, document_format)
        for page in pages:
            asked = self._asked(extracted, page, home, nodes)
            if asked is None:
                continue
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
                continue
            figures.extend(answered.figures)
        return tuple(figures)

    def derive(
        self,
        *,
        extracted: ExtractedDocument,
        source_path: str,
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None = None,
    ) -> HierarchyDerivation:
        """Read every page's outline, in page order.

        `on_page` hears each page as soon as it is settled -- its decisions, or None when the
        page got no usable answer -- so a long document shows what is read while the rest is
        still being read.
        """
        document_format = extracted.document.format
        if document_format not in SCOPE_FORMATS:
            return HierarchyDerivation()  # nothing else is rendered one page per authored unit
        nodes = {node.id: node for node in extracted.nodes}
        pages = self._pages(source_path, document_format)
        home = flow_pages(extracted, pages, document_format)
        flowing = document_format in FLOW_FORMATS
        if not flowing:
            return self._derive_apart(extracted, pages, home, nodes, on_page)
        decisions: list[HierarchyDecision] = []
        unread: list[int] = []
        seen: dict[str, HierarchyUnit] = {}
        placements = _Placements()
        carried = _Carried()
        tail: bytes | None = None
        for page in pages:
            asked = self._asked(extracted, page, home, nodes)
            if asked is None:
                continue
            scope, shown, crops = asked
            request = self._request(extracted, scope, shown, carried)
            try:
                answered = self._read(request, shown.png, crops, tail)
            except RuntimeError as error:
                # One page the model cannot answer is one page's outline, not the
                # document's. The rest was read, and this page keeps the flat outline it
                # already had rather than taking the whole pass down with it.
                _log.warning("%s: no usable answer, left as it was -- %s", scope.scope, error)
                unread.append(page.index)
                if on_page is not None:
                    on_page(page.index, None)
                continue
            decisions.extend(answered.decisions)
            placements.record(answered.decisions)
            if on_page is not None:
                on_page(page.index, answered.decisions)
            seen.update({unit.id: unit for unit in scope.units
                         if unit.id not in placements.dropped})
            carried = placements.carried(scope, seen)
            tail = _page_tail(shown.png)
        return HierarchyDerivation(decisions=tuple(decisions), unread_pages=tuple(unread))

    def _derive_apart(
        self,
        extracted: ExtractedDocument,
        pages: Sequence[RenderedPage],
        home: Mapping[str, int],
        nodes: Mapping[str, GraphNode],
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None,
    ) -> HierarchyDerivation:
        """Pages that stand alone, read `workers` at a time and each heard as it lands."""

        def read(page: RenderedPage) -> tuple[int, tuple[HierarchyDecision, ...] | None]:
            asked = self._asked(extracted, page, home, nodes)
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
    ) -> tuple[HierarchyScopeUnits, RenderedPage, dict[str, bytes] | None] | None:
        """What one page is asked with: its units, the page shown, and each picture's crop.

        The margins of a page carry nothing, so what goes out is the page cut back to what
        is drawn on it; the crops are cut from the page itself and are not touched by this,
        so no box moves.
        """
        scope = self._scope(extracted, page, extracted.document.format, home)
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
        )

    @staticmethod
    def _request(
        extracted: ExtractedDocument,
        scope: HierarchyScopeUnits,
        page: RenderedPage,
        carried: _Carried | None = None,
    ) -> HierarchyScopeRequest:
        carried = carried or _Carried()
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
                renderer_version=RENDERER_VERSION,
                mapping_hash=hashlib.sha256(
                    canonical_json([u.id for u in scope.units]).encode()
                ).hexdigest(),
                covered_unit_ids=unit_ids,
                manifest_hash=hashlib.sha256(
                    canonical_json(
                        {
                            "page": page.label or page.index,
                            "size": [page.width, page.height],
                        }
                    ).encode()
                ).hexdigest(),
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


@dataclass(frozen=True)
class _Carried:
    """The outline so far, as much of it as fits, and where this page attaches to it.

    `units` is in reading order, `parents` says who holds whom inside it, `open` is the
    path from the document down to where the page before stopped, and `omitted` counts the
    subtrees dropped under a unit to make room.
    """

    units: tuple[HierarchyUnit, ...] = ()
    parents: dict[str, str] = field(default_factory=dict)
    open: tuple[str, ...] = ()
    omitted: dict[str, int] = field(default_factory=dict)


class _Placements:
    """Where every unit read so far landed, kept as the pages are read.

    The page turn hands the next page the outline built up to here, so it is written down
    once as it is decided rather than rebuilt from every decision the document has made
    each time a page ends.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._children: dict[str, list[str]] = {}
        self.dropped: set[str] = set()
        """What a page dropped: never carried on, and never where a page stopped."""

    def record(self, decisions: Iterable[HierarchyDecision]) -> None:
        decisions = tuple(decisions)
        # Every drop first: a unit placed under one reads at the root, whatever the order.
        self.dropped |= dropped_subjects(decisions)
        for decision in decisions:
            if decision.status == "drop":
                continue
            parent = decision.parent_id
            if parent in self.dropped:
                continue  # placed under what was dropped: it reads at the root
            if decision.status != "parent" or parent is None:
                continue
            self._parent[decision.subject_id] = parent
            self._children.setdefault(parent, []).append(decision.subject_id)

    def carried(
        self, scope: HierarchyScopeUnits, seen: Mapping[str, HierarchyUnit]
    ) -> _Carried:
        """The outline so far, cut to the budget, still whole where it is kept.

        Two things travel. The open chain -- the last thing this page decided and whatever
        it reads under, up to the document -- is where the next page attaches, so it is
        always here even where its own branches were dropped. Everything else travels as
        complete subtrees: a unit comes with all of its descendants or it does not come at
        all. A heading whose items were dropped says nothing the next page can use; it
        reads as a section that started there and holds nothing.

        What a picture holds is left out of the chain: a page often ends inside a figure,
        and its last unit is then a word printed on that figure -- carried forward, the
        innermost thing still open would be a label on a drawing nobody can see any more.

        The newest subtrees are kept first. A section decided two pages ago is where this
        page continues; one decided at the start of the document is not.
        """
        spine = self._open_chain(scope, seen)
        kept = dict.fromkeys(spine)
        room = CARRY_TOKEN_BUDGET - sum(self._cost(unit_id, seen) for unit_id in spine)

        # The document's headings, shallowest first: without them a long document's early
        # chapters dropped out of the reference altogether once whole recent sections
        # filled it, and a late page was read with no idea its middle chapters existed.
        shape = int(CARRY_TOKEN_BUDGET * SKELETON_SHARE)
        depth = {unit_id: self._depth(unit_id, seen) for unit_id in seen}
        for heading in sorted(
            (unit_id for unit_id in seen if self._children.get(unit_id)),
            key=lambda unit_id: depth[unit_id],
        ):
            parent = self._parent.get(heading)
            if heading in kept or (parent in seen and parent not in kept):
                continue  # already here, or its parent is not -- the shape stays connected
            cost = self._cost(heading, seen)
            if cost > min(shape, room):
                continue
            kept[heading] = None
            shape -= cost
            room -= cost

        order = {unit_id: index for index, unit_id in enumerate(seen)}
        for subtree in self._offered(spine, seen, order):
            new = [unit_id for unit_id in subtree if unit_id not in kept]
            cost = sum(self._cost(unit_id, seen) for unit_id in new)
            if cost <= room:
                kept.update(dict.fromkeys(new))
                room -= cost

        units = tuple(
            # Sent at the length its cost was counted at.
            seen[unit_id].model_copy(
                update={"scope": scope.scope, "label": self._label(unit_id, seen)}
            )
            for unit_id in sorted(kept, key=lambda unit_id: order.get(unit_id, 0))
        )
        return _Carried(
            units=units,
            parents={
                unit_id: self._parent[unit_id]
                for unit_id in kept
                if self._parent.get(unit_id) in kept
            },
            open=spine,
            # What a carried unit holds and did not bring, so it does not read as a
            # section that began here and holds nothing.
            omitted={
                unit_id: missing
                for unit_id in kept
                if (
                    missing := sum(
                        1 for child in self._children.get(unit_id, ()) if child not in kept
                    )
                )
            },
        )

    def _depth(self, unit_id: str, seen: Mapping[str, HierarchyUnit]) -> int:
        depth, node = 0, self._parent.get(unit_id)
        while node is not None and node in seen and depth < CHAIN_DEPTH:
            depth, node = depth + 1, self._parent.get(node)
        return depth

    def _label(self, unit_id: str, seen: Mapping[str, HierarchyUnit]) -> str:
        limit = CARRY_LABEL_CHARS if self._children.get(unit_id) else CARRY_ITEM_CHARS
        return seen[unit_id].label[:limit]

    def _open_chain(
        self, scope: HierarchyScopeUnits, seen: Mapping[str, HierarchyUnit]
    ) -> tuple[str, ...]:
        """The path from the document down to where this page stopped, outermost first."""
        chain: list[str] = []  # innermost first, up to the document
        # Where the page stopped is its last unit that was kept: a page number below the text
        # is not what the next page continues.
        node_id: str | None = next(
            (unit.id for unit in reversed(scope.units) if unit.id in seen), None)
        while node_id is not None and node_id not in chain and len(chain) < CHAIN_DEPTH:
            chain.append(node_id)
            node_id = self._parent.get(node_id)

        def is_a_picture(unit_id: str) -> bool:
            unit = seen.get(unit_id)
            return unit is not None and unit.node_type in VISUAL_TYPES

        # A picture closes at the page break, and so does everything printed on it. Cutting
        # at the innermost one leaves the chain a reader would have: sections, not labels.
        drawn = [index for index, unit_id in enumerate(chain) if is_a_picture(unit_id)]
        still_open = chain[max(drawn) + 1 :] if drawn else chain
        return tuple(unit_id for unit_id in reversed(still_open) if unit_id in seen)

    def _offered(
        self,
        spine: Sequence[str],
        seen: Mapping[str, HierarchyUnit],
        order: Mapping[str, int],
    ) -> list[tuple[str, ...]]:
        """The complete subtrees that may travel beside the chain, newest first."""
        on_spine = set(spine)
        offered: list[tuple[str, ...]] = []
        for holder in (*spine, None):
            children = (
                self._children.get(holder, ())
                if holder is not None
                else [
                    unit_id
                    for unit_id in seen
                    if unit_id not in self._parent and unit_id not in on_spine
                ]
            )
            offered.extend(
                self._subtree(child)
                for child in children
                if child not in on_spine and child in seen
            )
        offered.sort(key=lambda subtree: order.get(subtree[0], 0), reverse=True)
        return offered

    def _subtree(self, root: str) -> tuple[str, ...]:
        """A unit and everything under it -- what travels together or not at all."""
        out: dict[str, None] = {}
        stack = [root]
        while stack:
            unit_id = stack.pop()
            if unit_id in out:
                continue
            out[unit_id] = None
            stack.extend(reversed(self._children.get(unit_id, ())))
        return tuple(out)

    def _cost(self, unit_id: str, seen: Mapping[str, HierarchyUnit]) -> int:
        label = self._label(unit_id, seen) if unit_id in seen else ""
        return ENTRY_TOKENS + estimated_tokens(label)


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
    return FlowPlacer(page.flow_boxes(), page.flow_drawings())


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

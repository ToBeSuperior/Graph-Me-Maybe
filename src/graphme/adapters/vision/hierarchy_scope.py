"""Derive a document's outline by showing each rendered page to a vision model.

Wired in only when the document asks for a derived outline. What the extraction read
off the file is sent -- unit text, cell ranges turned into pixels, whether a thing is a
table, a picture or text -- and what it judged is withheld, because a judged heading or
a parent link hands the model the answer it is being asked for.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image

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
from graphme.application.page_readings import (
    PageReadings,
    crop_hash,
    page_key,
    remembered,
    unit_entry,
)
from graphme.application.page_units import flow_pages, held_by_a_visual
from graphme.application.progress import Progress
from graphme.application.unit_placement import (
    FlowPlacer,
    NormalizedBoxPlacer,
    UnitPlacer,
)
from graphme.application.visual_crops import leads_a_figure, read_node_crop
from graphme.domain.enums import FLOW_FORMATS, DocumentFormat, NodeType
from graphme.domain.models import (
    ExtractedDocument,
    FigureGroupingResult,
    GraphNode,
    HeadingLine,
    HierarchyDecision,
    HierarchyDerivation,
    HierarchyFigureGroup,
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
    """The stored crop of every picture in the scope, and of the words leading a figure drawn
    in vectors (its title says what it is, not what it shows), keyed by the unit's id."""
    crops: dict[str, bytes] = {}
    for unit in units:
        node = nodes.get(unit.id)
        if node is None or (unit.node_type not in PICTURE_TYPES and not leads_a_figure(node)):
            continue
        crop = read_node_crop(node, asset_store=asset_store)
        if crop is not None:
            crops[unit.id] = crop
    return crops


MAX_SCOPE_UNITS = 500

_TooMany = Literal["too_many"]
TOO_MANY: _TooMany = "too_many"
"""A page past `MAX_SCOPE_UNITS`: not asked, and said unread -- not read as flat."""


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
        workers: int = 1,
        progress: Progress | None = None,
        kept: PageReadings | None = None,
    ) -> None:
        if readings < 1:
            raise ValueError("a scope needs at least one reading")
        self._workers = max(1, workers)
        """How many pages, and page breaks, are asked at once."""
        self._progress = progress or Progress()
        """Counts each page and page break asked, for whoever shows how far the reading is."""
        self._renderer = renderer
        self._vision = vision
        self._asset_store = asset_store
        self._kept = kept
        """What each page, the headings and each page break were answered, by what they
        hold: a page that did not change is not asked again."""
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
        held = held_by_a_visual(extracted)  # once a pass, not once a page
        pages = self._pages(source_path, document_format)
        home = flow_pages(extracted, pages, document_format)

        self._progress.plan("figures", len(pages))

        def read(page: RenderedPage) -> tuple[HierarchyFigureGroup, ...]:
            try:
                return figures_of(page)
            finally:
                self._progress.step("figures")

        def figures_of(page: RenderedPage) -> tuple[HierarchyFigureGroup, ...]:
            asked = self._asked(extracted, page, home, nodes, held)
            if not isinstance(asked, tuple):
                return ()
            scope, shown, crops = asked
            request = self._request(extracted, scope)
            try:
                answered, _ = remembered(
                    self._kept, _key("figures", scope, page, crops, nodes), _names(scope),
                    lambda: self._vision.group_page_figures(
                        request=request, image=shown.png, unit_images=crops),
                    load=FigureGroupingResult.model_validate,
                    dump=lambda found: found.model_dump(mode="json"),
                    document=extracted.document.content_hash)
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
        held = held_by_a_visual(extracted)  # once a pass, not once a page
        pages = self._pages(source_path, document_format)
        home = flow_pages(extracted, pages, document_format)
        flowing = document_format in FLOW_FORMATS
        if not flowing:
            self._progress.plan("outline", len(pages))
            return self._derive_apart(extracted, pages, home, nodes, held, on_page)
        found_on = {page.index: self._asked(extracted, page, home, nodes, held)
                    for page in pages}
        too_many = tuple(index for index, found in found_on.items() if found == TOO_MANY)
        asked = {index: found if isinstance(found, tuple) else None
                 for index, found in found_on.items()}
        scopes = [(index, found[0]) for index, found in asked.items() if found is not None]
        units = {unit.id: unit for _, scope in scopes for unit in scope.units}
        order = {unit.id: (index, place) for index, scope in scopes
                 for place, unit in enumerate(scope.units)}
        # The headings, a page each, a page break each; the breaks counted again once read.
        self._progress.plan("outline", 1 + len(scopes) + max(0, len(scopes) - 1))
        document = extracted.document.content_hash
        by_index = {page.index: page for page in pages}
        keys = {index: _key("outline", scope, by_index[index], found[2], nodes)
                for index, found in asked.items() if found is not None
                for scope in (found[0],)}
        heads = self._headings(scopes, document)
        self._progress.step("outline")
        read, reused = self._read_pages(extracted, pages, asked, heads, units, order, on_page,
                                        keys)
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
        self._progress.plan("outline", 1 + len(scopes) + len(requests))
        # A page break is kept by the two pages it joins: one beside a changed page is asked
        # again, the rest take what they were answered.
        before = {index: scopes[at - 1] for at, (index, _) in enumerate(scopes) if at}
        root_id = root.id

        def join(request: PageJoinRequest) -> dict[str, Answer]:
            index = request.page_index
            earlier, scope = before[index], asked[index]
            assert scope is not None
            names = (root_id, joining.top, *_names(earlier[1])[1:], *_names(scope[0])[1:])
            key = page_key([(keys[earlier[0]],), (keys[index],)], (), (), asked="join")
            return self._join(joining, request, key, names, document)

        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(requests)))) as pool:
            joined = dict(zip((r.page_index for r in requests), pool.map(join, requests),
                              strict=True))
        return HierarchyDerivation(
            decisions=joining.settle(joined),
            unread_pages=tuple(sorted(
                (*too_many, *(index for index, _ in scopes if read.get(index) is None)))),
            pages_read=len(scopes) - len(reused),
            pages_reused=len(reused),
        )

    def _headings(
        self, scopes: Sequence[tuple[int, HierarchyScopeUnits]], document: str
    ) -> tuple[HierarchyDecision, ...]:
        """The document's headings, read off its text, or none: past `MAX_HEADING_LINES`, or
        when the model gives no usable answer, the pages are read and joined without them."""
        lines = [HeadingLine(unit_id=unit.id, page=index, indent=unit.indent or 0,
                             label=unit.label)
                 for index, scope in scopes for unit in scope.units
                 if unit.node_type is NodeType.TEXT_CHUNK]
        if not lines or len(lines) > MAX_HEADING_LINES:
            return ()
        key = page_key([(line.label, line.page, line.indent) for line in lines], (), (),
                       asked="headings")
        try:
            answered, _ = remembered(
                self._kept, key, [line.unit_id for line in lines],
                lambda: self._vision.outline_headings(lines=lines),
                load=lambda said: tuple((str(u), None if p is None else str(p))
                                        for u, p in said),
                dump=lambda found: [list(pair) for pair in found],
                document=document)
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
        keys: Mapping[int, str],
    ) -> tuple[dict[int, tuple[HierarchyDecision, ...] | None], set[int]]:
        """Every page read on its own, side by side, shown the headings before it and the
        foot of the page before. Each is heard as it lands; None is a page left unread. A
        page kept under its key (`keys`) is not asked: it is among the pages returned reused.
        """
        reused: set[int] = set()
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
            request = self._request(extracted, scope, carried)
            try:
                answered, kept = remembered(
                    self._kept, keys[page.index], _names(scope),
                    lambda: self._read(request, shown.png, crops,
                                       _page_tail(previous[1].png) if previous else None,
                                       flowing=True),
                    load=HierarchyScopeResult.model_validate,
                    dump=lambda found: _on_page(found, scope),
                    document=extracted.document.content_hash)
                if kept:
                    reused.add(page.index)
            except RuntimeError as error:
                # One page the model cannot answer is one page's outline, not the document's.
                _log.warning("%s: no usable answer, left as it was -- %s", scope.scope, error)
                result: tuple[int, tuple[HierarchyDecision, ...] | None] = (page.index, None)
            else:
                result = (page.index, answered.decisions)
            self._progress.step("outline")
            if on_page is not None:
                on_page(*result)
            return result

        todo = [page for page in pages if asked.get(page.index) is not None]
        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(todo)))) as pool:
            return dict(pool.map(read, todo)), reused

    def _join(self, joining: Joining, request: PageJoinRequest, key: str,
              names: Sequence[str], document: str) -> dict[str, Answer]:
        """One page break: `JOIN_READINGS` readings, a third where they differ, the vote --
        or the vote kept under `key`, while every place it names is still offered."""
        failed = False

        def once() -> tuple[JoinPlacement, ...]:
            nonlocal failed
            try:
                return self._vision.join_page(request=request)
            except RuntimeError as error:
                _log.warning("page %d: no usable join -- %s", request.page_index, error)
                failed = True
                return ()

        def asked() -> dict[str, Answer]:
            readings = [once() for _ in range(JOIN_READINGS)]
            if joining.disagree(request, readings):
                readings.append(once())
            return joining.vote(request, readings)

        voted, _ = remembered(
            self._kept, key, names, asked, load=lambda said: _offered(said, request),
            dump=lambda found: None if failed else {u: list(a) for u, a in found.items()},
            document=document)
        self._progress.step("outline")
        return voted

    def _derive_apart(
        self,
        extracted: ExtractedDocument,
        pages: Sequence[RenderedPage],
        home: Mapping[str, int],
        nodes: Mapping[str, GraphNode],
        held: frozenset[str],
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None,
    ) -> HierarchyDerivation:
        """Pages that stand alone, read `workers` at a time and each heard as it lands."""
        reused: set[int] = set()
        tried: set[int] = set()

        def read(page: RenderedPage) -> tuple[int, tuple[HierarchyDecision, ...] | None]:
            asked = self._asked(extracted, page, home, nodes, held)
            if asked is None:
                self._progress.step("outline")
                return page.index, ()
            result: tuple[int, tuple[HierarchyDecision, ...] | None] = (page.index, None)
            if asked != TOO_MANY:
                scope, shown, crops = asked
                tried.add(page.index)
                try:
                    answered, kept = remembered(
                        self._kept, _key("outline", scope, page, crops, nodes), _names(scope),
                        lambda: self._read(self._request(extracted, scope), shown.png, crops),
                        load=HierarchyScopeResult.model_validate,
                        dump=lambda found: _on_page(found, scope),
                        document=extracted.document.content_hash)
                except RuntimeError as error:
                    _log.warning("%s: no usable answer, left as it was -- %s",
                                 scope.scope, error)
                else:
                    result = (page.index, answered.decisions)
                    if kept:
                        reused.add(page.index)
            self._progress.step("outline")
            if on_page is not None:
                on_page(*result)
            return result

        with ThreadPoolExecutor(max_workers=min(self._workers, max(1, len(pages)))) as pool:
            results = list(pool.map(read, pages))
        return HierarchyDerivation(
            decisions=tuple(d for _, found in results for d in (found or ())),
            unread_pages=tuple(index for index, found in results if found is None),
            pages_read=len(tried) - len(reused),
            pages_reused=len(reused),
        )

    def _asked(
        self,
        extracted: ExtractedDocument,
        page: RenderedPage,
        home: Mapping[str, int],
        nodes: Mapping[str, GraphNode],
        held: frozenset[str],
    ) -> tuple[HierarchyScopeUnits, RenderedPage, dict[str, bytes] | None] | _TooMany | None:
        """What one page is asked with: its units, the page shown, and each picture's crop;
        None for a page with nothing to ask, `TOO_MANY` for one past `MAX_SCOPE_UNITS`.

        The margins of a page carry nothing, so what goes out is the page cut back to what
        is drawn on it; the crops are cut from the page itself and are not touched by this,
        so no box moves.
        """
        scope = self._scope(extracted, page, extracted.document.format, home, held)
        if scope is None:
            return None
        if len(scope.units) > MAX_SCOPE_UNITS:
            _log.warning("%s: %d units, more than one ask holds -- left unread",
                         scope.scope, len(scope.units))
            return TOO_MANY
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
        *,
        flowing: bool = False,
    ) -> HierarchyScopeResult:
        """One page's outline, read as many times as asked and settled against the page. A
        flowing page's readings are compared by what its page keeps (`_kept`)."""
        def once() -> HierarchyScopeResult:
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
        compared = _kept if flowing else _stated
        if len({compared(reading, request) for reading in readings}) == 1:
            _log.info("%s: %d readings agreed", request.scope, len(readings))
            return readings[0]
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
        held: frozenset[str],
    ) -> HierarchyScopeUnits | None:
        """One page's units, placed the way that format states a position; `held` is what a
        visual holds (`held_by_a_visual`)."""
        if document_format in FLOW_FORMATS:
            root = next(
                (node for node in extracted.nodes if node.type is NodeType.DOCUMENT), None
            )
            if root is None:
                return None
            return build_placed_scope(
                extracted=extracted,
                held=held,
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
                held=held,
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
            held=held,
            sheet_name=page.label,
            scope=f"sheet-{page.index:02d}",
            page_index=page.index,
            grid=grid,
        )

    @staticmethod
    def _request(
        extracted: ExtractedDocument,
        scope: HierarchyScopeUnits,
        carried: Carried | None = None,
    ) -> HierarchyScopeRequest:
        carried = carried or Carried()
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
        )


def _names(scope: HierarchyScopeUnits) -> tuple[str, ...]:
    """The ids a page's reading may name, in the order its key lists them: the page's root,
    then its units."""
    return (scope.scope_root_id, *(unit.id for unit in scope.units))


def _key(asked: str, scope: HierarchyScopeUnits, page: RenderedPage,
         crops: Mapping[str, bytes] | None, nodes: Mapping[str, GraphNode]) -> str:
    """What one page is asked with: each unit's text, kind and place, its stored crop, and the
    page's size."""
    return page_key(
        [unit_entry(nodes[unit.id]) if unit.id in nodes
         else (unit.label, unit.node_type.value, None) for unit in scope.units],
        [crop_hash((crops or {}).get(unit.id)) for unit in scope.units],
        (page.width, page.height), asked=asked)


def _on_page(result: HierarchyScopeResult, scope: HierarchyScopeUnits) -> Any:
    """A page's reading as it is kept: a parent named on another page -- a heading carried
    in -- is the join's to decide and is not kept, so the unit stands at the root."""
    names = set(_names(scope))
    return HierarchyScopeResult(decisions=tuple(
        decision.model_copy(update={"status": "root", "parent_id": None})
        if decision.parent_id is not None and decision.parent_id not in names else decision
        for decision in result.decisions)).model_dump(mode="json")


def _offered(said: Any, request: PageJoinRequest) -> dict[str, Answer]:
    """A kept page break's answer, when it answers every top this break asks about and each
    names a place still offered or an earlier top; ValueError otherwise."""
    tops = [top.unit_id for top in request.tops]
    places = {place.unit_id for place in request.places}
    if not isinstance(said, dict) or set(said) != set(tops):
        raise ValueError("a kept join does not answer this page break")
    answers: dict[str, Answer] = {}
    for u, (how, at) in said.items():
        if how not in ("under", "beside") or not (
                at in places or at in tops[:tops.index(u)]):
            raise ValueError("a kept join names a place no longer offered")
        answers[u] = (how, at)
    return answers


_Said = frozenset[tuple[str, str, str | None]]


def _stated(result: HierarchyScopeResult,
            _request: HierarchyScopeRequest | None = None) -> _Said:
    """What a reading decided about every unit.

    Confidence and rationale are how a reading explains itself, not what it decided, so
    two readings that place everything the same way are the same reading here.
    """
    return frozenset(
        (decision.subject_id, decision.status, decision.parent_id)
        for decision in result.decisions
    )


def _kept(result: HierarchyScopeResult,
          request: HierarchyScopeRequest | None = None) -> _Said:
    """What a flowing page keeps of a reading (`PageTree`): what it drops, and each parent on
    the page. Root, unresolved and a parent on another page all leave a unit a top for the
    join, so readings differing only there are the same page."""
    on_page = {unit.id for unit in request.units} if request is not None else set()
    return frozenset(
        (decision.subject_id, "drop", None) if decision.status == "drop"
        else (decision.subject_id, "under", decision.parent_id)
        for decision in result.decisions
        if decision.status == "drop" or decision.parent_id in on_page
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

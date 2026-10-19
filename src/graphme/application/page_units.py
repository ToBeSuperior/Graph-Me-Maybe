"""What each rendered page holds, for the passes that ask a model about one page at a time.

Captioning and reference-finding ask different questions of the same thing: a page, the
units drawn on it, and the crop of each unit that is a picture. This is that shared part --
which page shows which unit, which of them another visual already holds, and how a page's
questions are run side by side under one shared limit.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol, TypeVar

from graphme.application.unit_placement import FlowPlacer, home_pages
from graphme.application.xlsx_visual_binding import VISUAL_TYPES, read_node_crop
from graphme.domain.enums import EdgeType, NodeType
from graphme.domain.models import ExtractedDocument, GraphNode
from graphme.ports.storage import AssetStore

_Job = TypeVar("_Job")
_Answer = TypeVar("_Answer")


class RenderedPageLike(Protocol):
    """A page as these passes need it: its number, its picture, and its name if it has one."""

    @property
    def index(self) -> int: ...

    @property
    def png(self) -> bytes: ...

    @property
    def label(self) -> str | None: ...


def flow_pages(
    extracted: ExtractedDocument, pages: Sequence[Any], document_format: Any
) -> Mapping[str, int]:
    """Where each node of a flowing document was drawn, or nothing for a format that says.

    A DOCX drawing knows the body element it hangs from, not the sheet of paper it landed
    on, so the layout is asked; every other format states a sheet, a slide or a page.
    """
    if getattr(document_format, "value", document_format) != "docx":
        return {}
    return home_pages(
        extracted.nodes,
        {page.index: FlowPlacer(page.flow_boxes(), page.flow_drawings()) for page in pages},
    )


def held_by_a_visual(extracted: ExtractedDocument) -> frozenset[str]:
    """What a table, a chart or a picture holds, and so is part of rather than beside.

    Whatever lies inside a table's frame belongs to that table -- the photographs in its
    cells, the boxes of a diagram -- and whatever the page said is drawn as part of a
    figure belongs to that figure. Either way the holder is the thing to ask about; asking
    again about each piece says nothing the holder's own answer does not.
    """
    types = {node.id: node.type for node in extracted.nodes}
    held = {
        edge.target_id
        for edge in extracted.edges
        if edge.type is EdgeType.CONTAINS and types.get(edge.source_id) in VISUAL_TYPES
    }
    return frozenset(
        held | {node.id for node in extracted.nodes if node.metadata.get("part_of_figure")}
    )


def units_by_page(
    extracted: ExtractedDocument,
    pages: Sequence[RenderedPageLike],
    *,
    kinds: Sequence[NodeType],
    page_of: Mapping[str, int] | None = None,
) -> Mapping[int, tuple[GraphNode, ...]]:
    """The units of these kinds each page shows, in the document's own order.

    `page_of` answers first, for a caller that has laid a flowing document out. What
    another visual holds is left out: it is read with its holder.
    """
    by_label = {page.label: page.index for page in pages if page.label}
    held = held_by_a_visual(extracted)
    wanted = set(kinds)
    placed = page_of or {}
    grouped: dict[int, list[GraphNode]] = {}
    for node in extracted.nodes:
        if node.type not in wanted or node.id in held:
            continue
        where = node.locator
        index = placed.get(node.id) or (
            by_label.get(where.sheet) if where.sheet is not None else where.slide or where.page
        )
        if index is None:
            continue
        grouped.setdefault(index, []).append(node)
    return {index: tuple(found) for index, found in grouped.items()}


def crops_of(
    units: Sequence[GraphNode],
    asset_store: AssetStore,
    *,
    kinds: Sequence[NodeType] | None = None,
) -> dict[str, bytes]:
    """The crop the binding stored for each unit -- never a fresh cut of the page."""
    wanted = None if kinds is None else set(kinds)
    return {
        node.id: crop
        for node in units
        if (wanted is None or node.type in wanted)
        and (crop := read_node_crop(node, asset_store=asset_store)) is not None
    }


def scope_of(page: RenderedPageLike, part: int = 1) -> str:
    """What one question about this page is called: a sheet has a name, a page a number."""
    where = f"sheet-{page.index:02d}" if page.label else f"page-{page.index:02d}"
    return where if part == 1 else f"{where}-{part}"


def asked_together(
    jobs: Sequence[_Job],
    ask: Callable[[_Job], _Answer],
    *,
    workers: int = 1,
    limiter: AbstractContextManager[Any] | None = None,
) -> list[_Answer]:
    """Run one question per job side by side, each call holding the caller's limit.

    A page is its own question here -- what one visual shows is not what the document is
    about -- so the pages go together; `limiter` is what keeps a caller reading several
    documents under what the endpoint takes.
    """
    held = limiter if limiter is not None else nullcontext()

    def asked(job: _Job) -> _Answer:
        with held:
            return ask(job)

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as pool:
        return list(pool.map(asked, jobs))

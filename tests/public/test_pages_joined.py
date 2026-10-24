"""A flowing document's pages are read on their own and joined at every page break.

Each page's reading is kept inside the page; where a page's top units go -- under what the
page before left open, or beside it as the next item of its kind -- is asked of each page
break, on text alone.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.vision.hierarchy_scope import RenderedHierarchyScopeProvider
from graphme.application.outline_join import Joining, PageTree
from graphme.domain.enums import DocumentFormat, EvidenceKind, NodeType
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphNode,
    HierarchyDecision,
    HierarchyScopeResult,
    HierarchyUnit,
    JoinPlacement,
    PageJoinRequest,
    SourceLocator,
)

PAGES = {  # page: (id, kind, parent on that page)
    1: [("title", NodeType.TEXT_CHUNK, None), ("version", NodeType.TEXT_CHUNK, "title"),
        ("intake", NodeType.TEXT_CHUNK, "title"), ("filter", NodeType.TEXT_CHUNK, "intake")],
    2: [("valve", NodeType.TEXT_CHUNK, None), ("outlet", NodeType.TEXT_CHUNK, None),
        ("hose", NodeType.TEXT_CHUNK, "outlet"), ("photo", NodeType.IMAGE, "outlet"),
        ("number", NodeType.TEXT_CHUNK, "drop")],
    3: [("costs", NodeType.TEXT_CHUNK, None)],
}
LABELS = {"title": "Pump Review", "version": "Version 2.0", "intake": "1. Intake",
          "filter": "- filter clean", "valve": "- valve sealed", "outlet": "2. Outlet",
          "hose": "- hose fine", "photo": "Image 1", "number": "2", "costs": "3. Costs"}
ANSWERS = {"valve": ("beside", "filter"), "outlet": ("beside", "intake"),
           "costs": ("beside", "outlet")}
"""What the page breaks answer: each top beside the unit of its kind left open."""


def _png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (600, 800), "white").save(out, format="PNG")
    return out.getvalue()


def _report() -> ExtractedDocument:
    where = SourceLocator(file_name="r.pdf", label="r.pdf")
    nodes = [GraphNode(id="doc", document_id="d", type=NodeType.DOCUMENT, label="r.pdf",
                       locator=where)]
    for page, units in PAGES.items():
        for i, (u, kind, _) in enumerate(units):
            nodes.append(GraphNode(
                id=u, document_id="d", type=kind, label=LABELS[u],
                text=None if kind is NodeType.IMAGE else LABELS[u],
                locator=SourceLocator(file_name="r.pdf", label=u, page=page),
                metadata={"normalized_bbox": [0.1, 0.1 + 0.1 * i, 0.9, 0.15 + 0.1 * i]},
                retrievable=kind is not NodeType.IMAGE))
    return ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r.pdf", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r.pdf")),
        nodes=tuple(nodes),
        edges=(),
        evidence=tuple(EvidenceRecord(id=f"ev-{n.id}", document_id="d", node_id=n.id,
                                      kind=EvidenceKind.SOURCE_TEXT, locator=n.locator,
                                      text=n.label)
                       for n in nodes if n.retrievable),
    )


class _Pages:
    def render(self, _source: Any, _fmt: Any) -> tuple[RenderedPage, ...]:
        return tuple(RenderedPage(index=i, width=600, height=800, png=_png()) for i in PAGES)


def _decision(u: str, parent: str | None) -> HierarchyDecision:
    if parent == "drop":
        return HierarchyDecision(subject_id=u, status="drop", confidence=0.9, rationale="r")
    return HierarchyDecision(subject_id=u, status="parent" if parent else "root",
                             parent_id=parent, confidence=0.9, rationale="r")


class _Vision:
    """Reads each page as the table says -- naming, on page 2, a parent on page 1 that the
    join is the one to decide -- and answers each page break from `ANSWERS`."""

    def __init__(self) -> None:
        self.joins: list[PageJoinRequest] = []

    def resolve_hierarchy_scope(self, *, request: Any, image: Any, unit_images: Any = None,
                                preceding: Any = None) -> HierarchyScopeResult:
        page = request.page_index
        said = [_decision(u, parent) for u, _, parent in PAGES[page]]
        if page == 2:
            said[0] = _decision("valve", "title")  # across the page break: not its to say
        return HierarchyScopeResult(decisions=tuple(said))

    def outline_headings(self, *, lines: Any) -> tuple[tuple[str, str | None], ...]:
        return ()

    def join_page(self, *, request: PageJoinRequest) -> tuple[JoinPlacement, ...]:
        self.joins.append(request)
        return tuple(JoinPlacement(subject_id=t.unit_id, place_id=ANSWERS[t.unit_id][1],
                                   how=ANSWERS[t.unit_id][0])  # type: ignore[arg-type]
                     for t in request.tops if t.unit_id in ANSWERS)


def _parents(derived: Any) -> dict[str, str | None]:
    return {d.subject_id: d.parent_id if d.status == "parent" else d.status
            for d in derived.decisions}


def test_each_page_break_is_joined_and_beside_takes_the_neighbours_parent() -> None:
    vision = _Vision()
    derived = RenderedHierarchyScopeProvider(renderer=_Pages(), vision=vision).derive(
        extracted=_report(), source_path="r.pdf")
    parents = _parents(derived)

    assert parents["title"] == "root"
    assert parents["version"] == "title"
    assert parents["valve"] == "intake"  # beside the item left open: in its list
    assert parents["outlet"] == "title"  # beside the chapter: the next chapter
    assert parents["costs"] == "title"
    assert parents["hose"] == "outlet" and parents["photo"] == "outlet"  # the page's own
    assert parents["number"] == "drop"
    # Page 3 may go beside page 2's tops; a picture or a dropped unit is never a place.
    offered = {p.unit_id for p in vision.joins[-1].places}
    assert {"outlet", "valve"} <= offered and not {"photo", "number"} & offered


def _joining(heads: tuple[HierarchyDecision, ...] = ()) -> Joining:
    trees = [PageTree.of(page, [u for u, _, _ in units],
                         [_decision(u, parent) for u, _, parent in units])
             for page, units in PAGES.items()]
    units = {u: HierarchyUnit(id=u, label=LABELS[u], scope=f"page-{page}", order=i + 1,
                              node_type=kind)
             for page, rows in PAGES.items() for i, (u, kind, _) in enumerate(rows)}
    order = {u: (page, i) for page, rows in PAGES.items() for i, (u, _, _) in enumerate(rows)}
    return Joining(root="doc", root_label="r.pdf", trees=trees, units=units, order=order,
                   heads=heads)


def _placed(u: str, at: str, how: str = "under") -> JoinPlacement:
    return JoinPlacement(subject_id=u, place_id=at, how=how)  # type: ignore[arg-type]


def test_the_title_is_the_first_pages_top_holding_the_most_and_is_always_place_zero() -> None:
    joining = _joining()
    assert joining.title == "title"
    request = joining.request(2)
    assert request is not None and request.places[0].unit_id == "title"
    # What a place holds is shown beside it: the title holds its version line and a chapter.
    assert request.places[0].held == 2


def test_a_tie_keeps_the_answer_opening_fewer_levels_and_beside_the_top_is_under_it() -> None:
    joining = _joining()
    request = joining.request(2)
    assert request is not None
    chosen = joining.vote(request, [[_placed("outlet", "intake", "beside")],
                                    [_placed("outlet", "filter", "under")]])
    assert chosen["outlet"] == ("beside", "intake")
    chosen = joining.vote(request, [[_placed("valve", "title", "beside")]] * 2)
    assert chosen["valve"] == ("under", "title")


def test_a_top_no_reading_answers_goes_under_the_innermost_section_open_there() -> None:
    joining = _joining()
    request = joining.request(2)
    assert request is not None
    chosen = joining.vote(request, [[], [_placed("valve", "nowhere")]])
    assert chosen["valve"] == ("under", "filter") and chosen["outlet"] == ("under", "filter")


def test_a_top_may_go_under_an_earlier_top_of_its_page_but_never_a_later_one() -> None:
    joining = _joining()
    request = joining.request(2)
    assert request is not None
    chosen = joining.vote(request, [[_placed("outlet", "valve"), _placed("valve", "outlet")]])
    assert chosen["outlet"] == ("under", "valve")
    assert chosen["valve"] == ("under", "filter")  # a later top is no place: unanswered

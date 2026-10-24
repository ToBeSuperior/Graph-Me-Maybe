"""Apply a derived outline onto the graph the deterministic pass produced.

The deterministic pass parents every unit from cell structure and markers. Measured
against a page a model reads directly, those links agreed only half the time, so a
document can ask for the outline to be derived from the rendering instead. This module
is the seam: it takes the decisions and rewrites the parent edges they name, leaving
every other edge -- and all native ownership -- exactly as the source stated it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from graphme.domain.enums import DocumentFormat, EdgeType
from graphme.domain.models import (
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    HierarchyDecision,
    HierarchyDerivation,
    HierarchyFigureGroup,
    SourceLocator,
    WarningRecord,
)

_PARENT_EDGE = EdgeType.OUTLINE_PARENT
"""The projection the deterministic pass emits. CONTAINS is native ownership and is
never rewritten -- a derived outline changes what reads under what, not what the
file says contains what."""


class HierarchyScopeProvider(Protocol):
    """Supplies what the page says: which units are one figure, and what holds what.

    Two passes over the same pages, in this order. A figure takes the words written on it
    before anything reads them as text, and the outline is drawn last, over whole units.
    """

    def group_figures(
        self, *, extracted: ExtractedDocument, source_path: str
    ) -> tuple[HierarchyFigureGroup, ...]: ...

    def derive(
        self,
        *,
        extracted: ExtractedDocument,
        source_path: str,
        on_page: Callable[[int, tuple[HierarchyDecision, ...] | None], None] | None = None,
        ready: Callable[[int], ExtractedDocument] | None = None,
    ) -> HierarchyDerivation: ...


def note_unread_pages(
    extracted: ExtractedDocument, pages: tuple[int, ...]
) -> ExtractedDocument:
    """Record each page the outline could not be read on, where an index keeps warnings."""
    if not pages:
        return extracted
    document = extracted.document
    slides = document.format is DocumentFormat.PPTX
    noted = tuple(
        WarningRecord(
            id=f"warn:hierarchy_page_unread:{document.id}:{page}",
            document_id=document.id,
            code="hierarchy_page_unread",
            message=f"no usable outline answer for page {page}; it keeps a flat outline",
            locator=SourceLocator(
                file_name=document.file_name,
                label=f"{'slide' if slides else 'page'} {page}",
                slide=page if slides else None,
                page=None if slides else page,
            ),
        )
        for page in pages
    )
    return extracted.model_copy(update={"warnings": (*extracted.warnings, *noted)})


def apply_hierarchy_decisions(
    extracted: ExtractedDocument,
    decisions: tuple[HierarchyDecision, ...],
) -> ExtractedDocument:
    """Rewrite the parent of every subject a decision names.

    A `root` decision removes the subject's parent; `unresolved` is an abstention and
    leaves the deterministic answer standing. A `drop` decision takes the subject out of the
    graph with its edges and evidence; what it owned is held by its owner instead -- a banner
    picture dropped as decoration keeps the title printed on it -- and a part drawn in a
    dropped figure is no longer held by it. A unit the same answer placed under a dropped one
    reads at the root. A decision naming a node that is not in the document is refused rather
    than silently ignored.
    """
    if not decisions:
        return extracted
    node_ids = {node.id for node in extracted.nodes}
    for decision in decisions:
        unknown = {decision.subject_id} - node_ids
        if decision.parent_id is not None:
            unknown |= {decision.parent_id} - node_ids
        if unknown:
            raise ValueError(f"hierarchy decision names unknown nodes: {sorted(unknown)}")

    dropped = dropped_subjects(decisions)

    replaced = {
        decision.subject_id: decision
        for decision in decisions
        if decision.status not in ("unresolved", "drop")
    }
    edges = [
        edge
        for edge in _held_by_the_kept(extracted.edges, dropped)
        if not (edge.type is _PARENT_EDGE and edge.target_id in replaced)
        and edge.source_id not in dropped and edge.target_id not in dropped
    ]
    for subject_id, decision in replaced.items():
        if subject_id in dropped:
            continue
        if decision.status != "parent" or decision.parent_id is None:
            continue  # a root decision is a valid flat result, not a missing parent
        if decision.parent_id in dropped:
            continue  # placed under what the same answer dropped: it reads at the root
        edges.append(
            GraphEdge(
                id=f"hierarchy:{decision.parent_id}->{subject_id}",
                document_id=extracted.document.id,
                source_id=decision.parent_id,
                target_id=subject_id,
                type=_PARENT_EDGE,
                metadata={
                    "basis": "derived_from_rendering",
                    "confidence": decision.confidence,
                },
            )
        )
    if not dropped:
        return extracted.model_copy(update={"edges": tuple(edges)})
    return extracted.model_copy(update={
        "nodes": tuple(
            _let_go(node, dropped) for node in extracted.nodes if node.id not in dropped),
        "edges": tuple(edges),
        "evidence": tuple(item for item in extracted.evidence if item.node_id not in dropped),
    })


def dropped_subjects(decisions: Iterable[HierarchyDecision]) -> set[str]:
    """The units an answer dropped -- taken before any of its other decisions is read, so a
    unit placed under a dropped one reads at the root whatever the order they came in."""
    return {decision.subject_id for decision in decisions if decision.status == "drop"}


def _held_by_the_kept(edges: Iterable[GraphEdge], dropped: set[str]) -> list[GraphEdge]:
    """What a dropped node owned (`CONTAINS`), owned by the nearest owner that stays: the one
    parent every node has is kept, and nothing it held is lost with it."""
    owner = {edge.target_id: edge.source_id for edge in edges if edge.type is EdgeType.CONTAINS}

    def kept(node_id: str) -> str | None:
        seen: set[str] = set()
        while node_id in dropped and node_id not in seen:
            seen.add(node_id)
            node_id = owner.get(node_id, "")
        return node_id or None

    out: list[GraphEdge] = []
    for edge in edges:
        moved = edge.source_id in dropped and edge.target_id not in dropped
        if edge.type is EdgeType.CONTAINS and moved:
            new_owner = kept(edge.source_id)
            if new_owner is not None:
                edge = edge.model_copy(update={"id": f"{edge.id}:held-by:{new_owner}",
                                               "source_id": new_owner})
        out.append(edge)
    return out


def _let_go(node: GraphNode, dropped: set[str]) -> GraphNode:
    """A part drawn in a dropped figure is no longer held by it."""
    if node.metadata.get("part_of_figure") not in dropped:
        return node
    return node.model_copy(update={"metadata": {
        key: value for key, value in node.metadata.items() if key != "part_of_figure"}})


def figure_part_decisions(extracted: ExtractedDocument) -> tuple[HierarchyDecision, ...]:
    """Every unit the page said is drawn as part of a figure, placed under that figure.

    The figure pass already answered this, so the outline pass is not asked again: a
    diagram's labels were sent to it one by one, most of a page's units, and it was left to
    place a drawing's labels among the paragraphs.
    """
    present = {node.id for node in extracted.nodes}
    return tuple(
        HierarchyDecision(
            subject_id=node.id,
            status="parent",
            parent_id=figure,
            confidence=1.0,
            rationale="drawn as part of this figure",
        )
        for node in extracted.nodes
        if isinstance(figure := node.metadata.get("part_of_figure"), str)
        and figure in present
        and figure != node.id
    )

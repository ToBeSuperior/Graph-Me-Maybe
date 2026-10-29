"""A caption for every table, chart and picture, written by the model that sees the page.

What a visual says is not in the file's text. A table's rows are values, a chart is a
series nobody wrote out, a photograph is a part nobody named -- and a search over the
document's words reaches none of it. So the page goes to the model with a crop of each
visual on it, and what comes back becomes a node of its own: a `vlm_enriched_caption`
held by the visual it describes (`CONTAINS`, so it has the one parent every node needs)
and pointing back at it with `CAPTION_OF`.

The caption is the model's, not the file's, and it says so: its evidence is
`GENERATED_CAPTION`, and it is not retrievable on its own -- the visual it describes is
what a reader asks for.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from graphme.application.hierarchy_decisions import apply_hierarchy_decisions
from graphme.application.page_units import (
    RenderedPageLike,
    asked_together,
    crops_of,
    scope_of,
    units_by_page,
)
from graphme.application.progress import Progress
from graphme.application.visual_crops import VISUAL_TYPES
from graphme.domain.enums import EdgeType, EvidenceKind, NodeType
from graphme.domain.ids import stable_id
from graphme.domain.models import (
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    HierarchyDecision,
    HierarchyUnit,
    VisualCaptionRequest,
    VisualCaptionResult,
)
from graphme.ports.storage import AssetStore
from graphme.ports.vision import VisionProvider

_log = logging.getLogger(__name__)

LABEL_CHARS = 80
CAPTIONS_PER_CALL = 6
"""How many visuals one call is asked to describe. A page of a parts catalogue holds
dozens, and asked for all of them at once the model answered with none -- every page with
more than about twenty came back empty while a page with eight was described in full."""
HEADING_LEVELS = 3
"""How far up the outline a caption is told about. The section a table sits in says what
it is for; the whole path to the document says no more than the first few steps of it."""
HEADING_CHARS = 120

_Job = tuple[RenderedPageLike, list[GraphNode], dict[str, bytes], int]


def caption_visuals(
    extracted: ExtractedDocument,
    *,
    pages: Sequence[RenderedPageLike],
    vision: VisionProvider,
    asset_store: AssetStore,
    workers: int = 1,
    progress: Progress | None = None,
    page_of: Mapping[str, int] | None = None,
) -> ExtractedDocument:
    """Add a caption node for every visual the pages show."""
    under = _headings_above(extracted)
    by_page = units_by_page(extracted, pages, kinds=VISUAL_TYPES, page_of=page_of)
    jobs: list[_Job] = [
        (page, batch, {node.id: crops[node.id] for node in batch}, part)
        for page in pages
        if (crops := crops_of(by_page.get(page.index, ()), asset_store))
        for part, batch in enumerate(
            # A backdrop with a crop is a scan whose words were read as the page's own.
            _batched([node for node in by_page[page.index]
                      if node.id in crops and not node.metadata.get("background")]), start=1
        )
    ]

    def describe(job: _Job) -> tuple[list[GraphNode], VisualCaptionResult | None]:
        page, described, crops, part = job
        request = _request_for(extracted, page, described, part, under)
        try:
            answer = vision.caption_visuals(
                request=request, image=page.png, unit_images=crops
            )
        except (RuntimeError, ValueError) as error:
            # One page without captions is one page's descriptions, not the document's.
            _log.warning("%s: no usable captions -- %s", request.scope, error)
            return described, None
        _log.info(
            "%s: %d of %d visuals described",
            request.scope, len(answer.captions), len(described),
        )
        return described, answer

    answers = asked_together(jobs, describe, workers=workers, progress=progress,
                             stage="captions")
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    evidence: list[EvidenceRecord] = []
    decorative: list[HierarchyDecision] = []
    for described, answer in answers:
        by_id = {node.id: node for node in described}
        for caption in answer.captions if answer is not None else ():
            if caption.decorative:
                # Seen up close, it carries nothing: dropped as the outline pass drops one,
                # so its words never become a passage's words. The outline pass is asked the
                # same of the whole page and misses an icon that has a word printed on it.
                decorative.append(HierarchyDecision(
                    subject_id=caption.subject_id, status="drop", parent_id=None,
                    confidence=caption.confidence, rationale="decorative, read from its crop"))
                continue
            node, pair, record = _caption_of(
                extracted, by_id[caption.subject_id], caption.caption
            )
            nodes.append(node)
            edges.extend(pair)
            evidence.append(record)
    if nodes:
        extracted = extracted.model_copy(
            update={
                "nodes": (*extracted.nodes, *nodes),
                "edges": (*extracted.edges, *edges),
                "evidence": (*extracted.evidence, *evidence),
            }
        )
    return apply_hierarchy_decisions(extracted, tuple(decorative))


def _batched(nodes: list[GraphNode]) -> list[list[GraphNode]]:
    return [nodes[at : at + CAPTIONS_PER_CALL] for at in range(0, len(nodes), CAPTIONS_PER_CALL)]


def _headings_above(extracted: ExtractedDocument) -> Mapping[str, str]:
    """What each node reads under, outermost first, as one line.

    The outline decided this a moment ago, and it is what turns "a table of numbers" into
    what that table is for.
    """
    parent = {
        edge.target_id: edge.source_id
        for edge in extracted.edges
        if edge.type is EdgeType.OUTLINE_PARENT
    }
    for edge in extracted.edges:
        if edge.type is EdgeType.CONTAINS:
            parent.setdefault(edge.target_id, edge.source_id)
    labels = {node.id: node.label for node in extracted.nodes}
    said: dict[str, str] = {}
    for node in extracted.nodes:
        chain: list[str] = []
        at = parent.get(node.id)
        while at is not None and at not in chain and len(chain) < HEADING_LEVELS:
            chain.append(at)
            at = parent.get(at)
        written = " > ".join(labels[found][:HEADING_CHARS] for found in reversed(chain))
        if written:
            said[node.id] = written
    return said


def _request_for(
    extracted: ExtractedDocument,
    page: RenderedPageLike,
    described: Sequence[GraphNode],
    part: int,
    under: Mapping[str, str],
) -> VisualCaptionRequest:
    scope = scope_of(page, part)
    return VisualCaptionRequest(
        request_id=f"{extracted.document.id}:caption:{scope}",
        document_id=extracted.document.id,
        scope=scope,
        page_index=max(1, page.index),
        units=tuple(
            HierarchyUnit(
                id=node.id, label=node.label, scope=scope, order=order, node_type=node.type
            )
            for order, node in enumerate(described, start=1)
        ),
        under={node.id: said for node in described if (said := under.get(node.id))},
    )


def _caption_of(
    extracted: ExtractedDocument, subject: GraphNode, caption: str
) -> tuple[GraphNode, tuple[GraphEdge, GraphEdge], EvidenceRecord]:
    caption_id = stable_id(extracted.document.content_hash, f"caption:{subject.id}")
    node = GraphNode(
        id=caption_id,
        document_id=extracted.document.id,
        type=NodeType.VLM_ENRICHED_CAPTION,
        label=caption[:LABEL_CHARS],
        text=caption,
        locator=subject.locator,
        # A caption is the model's reading of the visual, not something the file says, so
        # it is not what a search returns -- the visual it describes is.
        retrievable=False,
        metadata={"caption_of": subject.id},
    )
    held = GraphEdge(
        id=f"contains:{subject.id}->{caption_id}",
        document_id=extracted.document.id,
        source_id=subject.id,
        target_id=caption_id,
        type=EdgeType.CONTAINS,
    )
    describes = GraphEdge(
        id=f"caption:{caption_id}->{subject.id}",
        document_id=extracted.document.id,
        source_id=caption_id,
        target_id=subject.id,
        type=EdgeType.CAPTION_OF,
    )
    record = EvidenceRecord(
        id=stable_id(extracted.document.content_hash, f"evidence:caption:{subject.id}"),
        document_id=extracted.document.id,
        node_id=caption_id,
        kind=EvidenceKind.GENERATED_CAPTION,
        locator=subject.locator,
        text=caption,
        generated=True,
    )
    return node, (held, describes), record

"""The other documents a document names, as nodes it points at.

A report points at an earlier report by its number, a memo answers a memo, a paper cites
a paper. The named document is not here to be read, and often is not held at all -- but
the mention is a fact of this page, and losing it loses the only thread between the two.

So every distinct name becomes one `external_document` node: empty, because nothing here
knows what is in it, and held by the document that names it (`CONTAINS`, the one parent
every node needs). The unit that wrote the name points at it with `REFERENCES`. Which
indexed document the name is, if any, is said across the corpus once both are there
(`application/corpus_links.py`), not here, where only this document is known.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any

from graphme.application.corpus_links import document_numbers, generic_name
from graphme.application.page_units import (
    RenderedPageLike,
    asked_together,
    crops_of,
    scope_of,
    units_by_page,
)
from graphme.application.text_match import folded, without
from graphme.application.xlsx_visual_binding import CONTAINER_TYPES, VISUAL_TYPES
from graphme.domain.enums import EdgeType, NodeType
from graphme.domain.ids import stable_id
from graphme.domain.models import (
    DocumentReference,
    DocumentReferenceRequest,
    DocumentReferenceResult,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    HierarchyUnit,
    SourceLocator,
)
from graphme.ports.vision import VisionProvider

_log = logging.getLogger(__name__)

NAMING_TYPES = (NodeType.TEXT_CHUNK, *VISUAL_TYPES)
"""What can name another document: words, a table of references, a drawing citing a
standard. Each goes out the way the hierarchy pass sends one -- an id and a label, and a
crop where the unit is a picture; the page itself carries the rest."""

UNITS_PER_ASK = 500
"""What one ask can hold (`DocumentReferenceRequest.units`). A page with more -- a long
sheet -- is asked in parts; failing the ask lost the whole document."""

_Job = tuple[RenderedPageLike, tuple[GraphNode, ...], int]


def link_references(
    extracted: ExtractedDocument,
    *,
    pages: Sequence[RenderedPageLike],
    vision: VisionProvider,
    asset_store: Any = None,
    page_of: Mapping[str, int] | None = None,
    workers: int = 1,
    limiter: AbstractContextManager[Any] | None = None,
) -> ExtractedDocument:
    """Add a node for every other document this one names, and point the mention at it."""
    by_page = units_by_page(extracted, pages, kinds=NAMING_TYPES, page_of=page_of)
    jobs: list[_Job] = [
        (page, units[at : at + UNITS_PER_ASK], at // UNITS_PER_ASK + 1)
        for page in pages
        if (units := by_page.get(page.index))
        for at in range(0, len(units), UNITS_PER_ASK)
    ]

    def read(job: _Job) -> DocumentReferenceResult | None:
        page, units, part = job
        request = _request_for(extracted, page, units, part)
        crops = (
            crops_of(units, asset_store, kinds=VISUAL_TYPES) or None
            if asset_store is not None
            else None
        )
        try:
            answer = vision.find_references(
                request=request, image=page.png, unit_images=crops
            )
        except (RuntimeError, ValueError) as error:
            # One page nobody could read for references is one page, not the document.
            _log.warning("%s: no usable references -- %s", request.scope, error)
            return None
        _log.info("%s: %d references named", request.scope, len(answer.references))
        return answer

    texts = {node.id: node.text for node in extracted.nodes}
    tabled = _in_tables(extracted)
    citations: list[Citation] = []
    for answer in asked_together(jobs, read, workers=workers, limiter=limiter):
        for reference in answer.references if answer is not None else ():
            name = reference.name.strip()
            if not name or not _written(reference, texts.get(reference.subject_id)):
                continue
            if generic_name(name):
                # "the attached checklist", "본 문서", a bare number: it points at something,
                # but at no document anyone could find by those words.
                _log.info("reference %r dropped: a generic name", name[:40])
                continue
            if _only_a_mark(reference) and (
                reference.subject_id in tabled
                or not folded(texts.get(reference.subject_id) or "").startswith(
                    folded(_mark(reference))
                )
            ):
                # A citation mark only points at the reference list. Beside a method in a
                # sentence, or on a row of a results table, it names that thing and sends
                # the reader nowhere; the document is named in the list's entry, which is
                # the unit that begins with the mark.
                _log.info("reference %r dropped: a citation mark, not a document", name[:40])
                continue
            citations.append((reference.subject_id, name, {
                "named_as": name,
                "cue": reference.cue,
                "kind": reference.kind,
                "confidence": reference.confidence,
            }))
    return cite(extracted, citations)


Citation = tuple[str, str, dict[str, Any]]
"""Who names a document, the name, and what the citing edge records of it."""


def cite(extracted: ExtractedDocument, citations: Iterable[Citation]) -> ExtractedDocument:
    """Every distinct name as one `external_document` node the document holds, pointed at by
    each unit that names it. A unit naming it twice states it once, and a name the graph
    already holds is the node already there."""
    root = extracted.document.id
    held = {node.id for node in extracted.nodes} | {edge.id for edge in extracted.edges}
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    for subject, name, metadata in citations:
        node = _referenced(extracted, name)
        if node.id not in held:
            nodes.setdefault(node.id, node)
        holds = f"contains:{root}->{node.id}"
        if holds not in held:
            edges.setdefault(holds, GraphEdge(
                id=holds, document_id=root, source_id=root, target_id=node.id,
                type=EdgeType.CONTAINS,
            ))
        names = f"references:{subject}->{node.id}"
        if names not in held:
            edges.setdefault(names, GraphEdge(
                id=names, document_id=root, source_id=subject, target_id=node.id,
                type=EdgeType.REFERENCES, metadata=metadata,
            ))
    if not edges:
        return extracted
    return extracted.model_copy(update={
        "nodes": (*extracted.nodes, *nodes.values()),
        "edges": (*extracted.edges, *edges.values()),
    })

_MARK = re.compile(
    r"^(?:\[\s*\d+(?:\s*[-,–]\s*\d+)*\s*\]|\(\s*\d+\s*\)|\d+\.|"
    r"\([^()]*\b(?:19|20)\d{2}[a-z]?\))$"
)
"""A citation mark and nothing else: "[7]", "[3, 5]", "(2)", "7.", "(Kim, 2021)"."""


_EXAMPLE = re.compile(r"^(?:예\s*[):.]|예시|예를 들어|e\.g\.|eg\.|ex\.|for example|example)")
"""What a name given as an example is introduced by: it shows a form, it cites nothing."""


def _mark(reference: DocumentReference) -> str:
    """The cue with the name taken out, whatever its case -- the mark, when the cue is the
    name and a mark."""
    return without(reference.cue, reference.name)


def _only_a_mark(reference: DocumentReference) -> bool:
    return bool(_MARK.match(_mark(reference)))


def _in_tables(extracted: ExtractedDocument) -> frozenset[str]:
    """Every table, and whatever lies inside one or was drawn as part of one."""
    tables = {node.id for node in extracted.nodes if node.type in CONTAINER_TYPES}
    inside = {
        edge.target_id
        for edge in extracted.edges
        if edge.type is EdgeType.CONTAINS and edge.source_id in tables
    }
    parts = {
        node.id for node in extracted.nodes if node.metadata.get("part_of_figure") in tables
    }
    return frozenset(tables | inside | parts)


MIN_CUE_CHARS = 2
"""A bare footnote digit is in every row of a table, so finding it proves nothing; a mark
that can be checked is written as one -- "[4]", "(Kim, 2021)", a word, an address."""


def _written(reference: DocumentReference, text: str | None) -> bool:
    """Whether the unit really writes the name and the words that point at it.

    A citation is something the page says, so both have to be on it. The model reads the
    page and can name what a sentence is about -- a method, a model -- as though it were
    cited, or supply the pointing words itself; a cue the unit does not contain was never
    written there. Nor is a cue that is only the name again: a name mentioned in passing has
    no pointing words, and offering the name as its own cue says so. An address is the
    exception -- a URL is its own pointer -- and so is a document number: "XQ-2031-0042"
    is what makes the name a document's. A unit with no text of its own, a picture, is
    read from its crop and there is nothing to hold the answer to, so it is taken as given.
    """
    cue, name = folded(reference.cue), folded(reference.name)
    if _EXAMPLE.match(folded(_mark(reference))):
        _log.info("reference %r dropped: given as an example", reference.name[:40])
        return False
    if len(cue) < MIN_CUE_CHARS:
        _log.info("reference %r dropped: its cue %r proves nothing", reference.name[:40], cue)
        return False
    if reference.kind != "address" and cue in name and not document_numbers(reference.name):
        _log.info("reference %r dropped: its cue is only its name", reference.name[:40])
        return False
    if not text:
        return True
    written = folded(text)
    found = cue in written and name in written
    if not found:
        _log.info("reference %r dropped: not written as cited", reference.name[:40])
    return found


def _referenced(
    extracted: ExtractedDocument,
    name: str,
) -> GraphNode:
    return GraphNode(
        id=stable_id(extracted.document.content_hash, f"reference:{name}"),
        document_id=extracted.document.id,
        type=NodeType.EXTERNAL_DOCUMENT,
        label=name,
        # Empty on purpose: what is in the named document is not known here.
        text=None,
        locator=SourceLocator(file_name=extracted.document.file_name, label=name),
        retrievable=False,
    )


def _request_for(
    extracted: ExtractedDocument,
    page: RenderedPageLike,
    units: Sequence[GraphNode],
    part: int = 1,
) -> DocumentReferenceRequest:
    scope = scope_of(page, part)
    return DocumentReferenceRequest(
        request_id=f"{extracted.document.id}:references:{scope}",
        document_id=extracted.document.id,
        scope=scope,
        page_index=max(1, page.index),
        units=tuple(
            HierarchyUnit(
                id=node.id,
                label=_sentence(node),
                scope=scope,
                order=order,
                node_type=node.type,
            )
            for order, node in enumerate(units, start=1)
        ),
    )


TEXT_CHARS = 600
"""How much of a unit's text goes out. A label is its opening words, and the words that point
at a document -- "참고하라", "see", the citation mark -- usually come at the end."""


def _sentence(node: GraphNode) -> str:
    """A text unit as written, up to TEXT_CHARS; anything else by its label."""
    if node.type is NodeType.TEXT_CHUNK and node.text and node.text.strip():
        return " ".join(node.text.split())[:TEXT_CHARS]
    return node.label

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graphme.domain.enums import EdgeType, RelationBasis
from graphme.domain.ids import canonical_json, stable_id
from graphme.domain.models import (
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    RelationEvidenceContext,
    RelationNodeContext,
    RelationProposal,
    RelationRequest,
    SourceLocator,
    WarningRecord,
)
from graphme.ports.vision import VisionProvider

_ALLOWED_TYPES: dict[RelationBasis, frozenset[EdgeType]] = {
    RelationBasis.HIERARCHY: frozenset(
        {EdgeType.CONTAINS, EdgeType.PARENT_OF}
    ),
    RelationBasis.DOCUMENT_SEQUENCE: frozenset({EdgeType.NEXT}),
    RelationBasis.EXPLICIT_REFERENCE: frozenset(
        {
            EdgeType.REFERENCES,
            EdgeType.USES_VALUE_FROM,
            EdgeType.CAPTION_OF,
            EdgeType.RELATED_TO,
        }
    ),
    RelationBasis.TEMPORAL: frozenset(),
    RelationBasis.UNRESOLVED: frozenset(),
}
_STRUCTURAL_TYPES = {
    EdgeType.CONTAINS,
    EdgeType.PARENT_OF,
    EdgeType.NEXT,
    EdgeType.SAME_SHEET,
}


@dataclass(frozen=True, slots=True)
class _Rejected:
    code: str
    message: str
    proposal: RelationProposal


class RelationReconciler:
    """Bound model relation proposals and reconcile them deterministically."""

    def __init__(self, *, provider: VisionProvider) -> None:
        self._provider = provider

    def reconcile(self, extracted: ExtractedDocument) -> ExtractedDocument:
        nodes_by_id = {node.id: node for node in extracted.nodes}
        evidence_by_id = {record.id: record for record in extracted.evidence}
        pages = sorted(
            {
                node.locator.page
                for node in extracted.nodes
                if node.retrievable and node.locator.page is not None
            }
        )
        windows = self._page_windows(pages)
        proposals: list[RelationProposal] = []
        for page_indexes in windows:
            request = self._request(
                extracted=extracted,
                page_indexes=page_indexes,
            )
            proposals.extend(
                self._provider.propose_relations(request=request)
            )

        accepted, rejected = self._reconcile_proposals(
            extracted=extracted,
            proposals=tuple(proposals),
            nodes_by_id=nodes_by_id,
            evidence_by_id=evidence_by_id,
        )
        warnings = tuple(
            sorted(
                (*extracted.warnings, *(self._warning(extracted, item) for item in rejected)),
                key=lambda item: item.id,
            )
        )
        reconciled = ExtractedDocument(
            document=extracted.document,
            nodes=extracted.nodes,
            edges=(*extracted.edges, *accepted),
            evidence=extracted.evidence,
            warnings=warnings,
        )
        return reconciled.validate_canonical_semantic_graph()

    @staticmethod
    def _page_windows(pages: list[int]) -> tuple[tuple[int, ...], ...]:
        if not pages:
            return ()
        if len(pages) == 1:
            return ((pages[0],),)
        return tuple(
            (left, right)
            for left, right in zip(pages, pages[1:], strict=False)
        )

    @staticmethod
    def _request(
        *,
        extracted: ExtractedDocument,
        page_indexes: tuple[int, ...],
    ) -> RelationRequest:
        page_set = set(page_indexes)
        nodes = tuple(
            node
            for node in extracted.nodes
            if node.retrievable and node.locator.page in page_set
        )
        nodes = tuple(
            sorted(
                nodes,
                key=lambda item: (
                    item.locator.page or 0,
                    RelationReconciler._reading_order(item),
                    item.id,
                ),
            )
        )
        node_ids = tuple(node.id for node in nodes)
        evidence = tuple(
            sorted(
                (
                    record
                    for record in extracted.evidence
                    if record.node_id in set(node_ids)
                    and record.locator.page in page_set
                ),
                key=lambda item: (
                    item.locator.page or 0,
                    item.node_id,
                    item.id,
                ),
            )
        )
        evidence_ids = tuple(record.id for record in evidence)
        request_id = stable_id(
            extracted.document.content_hash,
            "relation-request:pages="
            + ",".join(str(page) for page in page_indexes),
        )
        return RelationRequest(
            request_id=request_id,
            node_ids=node_ids,
            evidence_ids=evidence_ids,
            page_indexes=page_indexes,
            nodes=tuple(RelationReconciler._node_context(node) for node in nodes),
            evidence=tuple(
                RelationReconciler._evidence_context(record)
                for record in evidence
            ),
        )

    @staticmethod
    def _reading_order(node: GraphNode) -> int:
        value = node.metadata.get("reading_order")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return 0

    @staticmethod
    def _node_context(node: GraphNode) -> RelationNodeContext:
        return RelationNodeContext(
            id=node.id,
            type=node.type,
            label=node.label,
            page_index=node.locator.page or 1,
            interpreted_text=node.text,
        )

    @staticmethod
    def _evidence_context(
        record: EvidenceRecord,
    ) -> RelationEvidenceContext:
        summary = record.text
        if summary is None and record.data is not None:
            summary = canonical_json(record.data)
        return RelationEvidenceContext(
            id=record.id,
            node_id=record.node_id,
            kind=record.kind,
            page_index=record.locator.page or 1,
            generated=record.generated,
            summary=summary,
        )

    def _reconcile_proposals(
        self,
        *,
        extracted: ExtractedDocument,
        proposals: tuple[RelationProposal, ...],
        nodes_by_id: dict[str, GraphNode],
        evidence_by_id: dict[str, EvidenceRecord],
    ) -> tuple[tuple[GraphEdge, ...], tuple[_Rejected, ...]]:
        accepted: list[GraphEdge] = []
        rejected: list[_Rejected] = []
        existing_keys = {
            (edge.source_id, edge.target_id, edge.type)
            for edge in extracted.edges
        }
        hierarchy = self._hierarchy(extracted.edges)
        proposal_keys: set[tuple[str, str, EdgeType]] = set()
        for proposal in sorted(proposals, key=self._proposal_sort_key):
            rejection = self._validate(
                proposal=proposal,
                nodes_by_id=nodes_by_id,
                evidence_by_id=evidence_by_id,
                existing_keys=existing_keys,
                proposal_keys=proposal_keys,
                hierarchy=hierarchy,
            )
            if rejection is not None:
                rejected.append(rejection)
                continue
            key = (proposal.source_id, proposal.target_id, proposal.type)
            proposal_keys.add(key)
            edge = self._edge(extracted, proposal, nodes_by_id)
            accepted.append(edge)
            if proposal.type in {EdgeType.CONTAINS, EdgeType.PARENT_OF}:
                hierarchy[proposal.source_id].add(proposal.target_id)
        return tuple(accepted), tuple(rejected)

    @staticmethod
    def _validate(
        *,
        proposal: RelationProposal,
        nodes_by_id: dict[str, GraphNode],
        evidence_by_id: dict[str, EvidenceRecord],
        existing_keys: set[tuple[str, str, EdgeType]],
        proposal_keys: set[tuple[str, str, EdgeType]],
        hierarchy: dict[str, set[str]],
    ) -> _Rejected | None:
        if proposal.source_id not in nodes_by_id or proposal.target_id not in nodes_by_id:
            return _Rejected(
                "relation_unknown_endpoint",
                "Relation proposal references an unknown node.",
                proposal,
            )
        if proposal.source_id == proposal.target_id:
            return _Rejected(
                "relation_self_edge",
                "Relation proposal forms a self edge.",
                proposal,
            )
        if any(item not in evidence_by_id for item in proposal.evidence_ids):
            return _Rejected(
                "relation_unknown_evidence",
                "Relation proposal references unknown evidence.",
                proposal,
            )
        source_page = nodes_by_id[proposal.source_id].locator.page
        target_page = nodes_by_id[proposal.target_id].locator.page
        required_pages = {page for page in (source_page, target_page) if page is not None}
        if not required_pages.issubset(proposal.provenance_page_indexes):
            return _Rejected(
                "relation_missing_provenance",
                "Relation proposal does not cover both endpoint pages.",
                proposal,
            )
        if proposal.basis is RelationBasis.TEMPORAL:
            return _Rejected(
                "relation_temporal_unsupported",
                "Temporal relations are not supported by the edge vocabulary.",
                proposal,
            )
        if proposal.basis is RelationBasis.UNRESOLVED:
            return _Rejected(
                "relation_unresolved",
                "Unresolved relation proposal was not accepted.",
                proposal,
            )
        if proposal.type not in _ALLOWED_TYPES[proposal.basis]:
            return _Rejected(
                "relation_incompatible_basis",
                "Relation type is incompatible with its proposal basis.",
                proposal,
            )
        key = (proposal.source_id, proposal.target_id, proposal.type)
        if proposal.type in _STRUCTURAL_TYPES:
            return _Rejected(
                "relation_structural_edge",
                "Relation proposals cannot alter structural edges.",
                proposal,
            )
        if key in existing_keys or key in proposal_keys:
            return _Rejected(
                "relation_duplicate",
                "Duplicate relation proposal was not accepted.",
                proposal,
            )
        if proposal.type in {EdgeType.CONTAINS, EdgeType.PARENT_OF} and self_reachable(
            hierarchy, proposal.target_id, proposal.source_id
        ):
            return _Rejected(
                "relation_hierarchy_cycle",
                "Relation proposal would create a hierarchy cycle.",
                proposal,
            )
        return None

    @staticmethod
    def _hierarchy(edges: tuple[GraphEdge, ...]) -> dict[str, set[str]]:
        hierarchy: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            if edge.type in {EdgeType.CONTAINS, EdgeType.PARENT_OF}:
                hierarchy[edge.source_id].add(edge.target_id)
        return hierarchy

    @staticmethod
    def _edge(
        extracted: ExtractedDocument,
        proposal: RelationProposal,
        nodes_by_id: dict[str, GraphNode],
    ) -> GraphEdge:
        proposal_json = canonical_json(proposal.model_dump(mode="json"))
        locator = RelationReconciler._proposal_locator(proposal, nodes_by_id)
        return GraphEdge(
            id=stable_id(
                extracted.document.content_hash,
                f"relation:{proposal_json}",
            ),
            document_id=extracted.document.id,
            source_id=proposal.source_id,
            target_id=proposal.target_id,
            type=proposal.type,
            locator=locator,
            metadata={
                "basis": proposal.basis.value,
                "confidence": proposal.confidence,
                "evidence_ids": list(proposal.evidence_ids),
                "model_generated": True,
                "provenance_page_indexes": list(
                    proposal.provenance_page_indexes
                ),
                "rationale": proposal.rationale,
            },
        )

    @staticmethod
    def _proposal_locator(
        proposal: RelationProposal,
        nodes_by_id: dict[str, GraphNode],
    ) -> SourceLocator:
        source = nodes_by_id[proposal.source_id].locator
        return source.model_copy(
            update={
                "metadata": {
                    **source.metadata,
                    "relation_provenance_page_indexes": list(
                        proposal.provenance_page_indexes
                    ),
                }
            }
        )

    @staticmethod
    def _warning(
        extracted: ExtractedDocument,
        rejected: _Rejected,
    ) -> WarningRecord:
        proposal_json = canonical_json(
            rejected.proposal.model_dump(mode="json")
        )
        node = next(
            (
                item
                for item in extracted.nodes
                if item.id == rejected.proposal.source_id
            ),
            None,
        )
        return WarningRecord(
            id=stable_id(
                extracted.document.content_hash,
                f"warning:{rejected.code}:{proposal_json}",
            ),
            document_id=extracted.document.id,
            code=rejected.code,
            message=rejected.message,
            locator=node.locator if node is not None else None,
        )

    @staticmethod
    def _proposal_sort_key(proposal: RelationProposal) -> str:
        return canonical_json(proposal.model_dump(mode="json"))


def self_reachable(
    hierarchy: dict[str, set[str]],
    start: str,
    target: str,
) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id == target:
            return True
        if node_id in seen:
            continue
        seen.add(node_id)
        pending.extend(hierarchy.get(node_id, ()))
    return False

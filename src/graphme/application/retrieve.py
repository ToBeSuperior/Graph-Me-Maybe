from math import sqrt

from graphme.domain.enums import EdgeType
from graphme.domain.models import (
    EmbeddingRecord,
    EvidenceRecord,
    GraphEdge,
    GraphNode,
    RetrievalHit,
    RetrievedContext,
    SubgraphRecord,
)

_RELATION_PRIORITY = (
    EdgeType.USES_VALUE_FROM,
    EdgeType.REFERENCES,
    EdgeType.CONTAINS,
    EdgeType.PARENT_OF,
    EdgeType.SAME_SHEET,
)
_HIERARCHY_EDGES = {EdgeType.CONTAINS, EdgeType.PARENT_OF}


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions must match")
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def rank_subgraphs(
    *,
    query_vector: tuple[float, ...],
    subgraphs: tuple[SubgraphRecord, ...],
    embeddings: tuple[EmbeddingRecord, ...],
) -> tuple[tuple[RetrievalHit, ...], dict[str, float]]:
    subgraphs_by_id = {subgraph.id: subgraph for subgraph in subgraphs}
    hits = tuple(
        sorted(
            (
                RetrievalHit(
                    subgraph_id=subgraph.id,
                    score=_cosine_similarity(query_vector, embedding.vector),
                    seed_node_id=subgraph.seed_node_id,
                    node_ids=subgraph.node_ids,
                )
                for embedding in embeddings
                if (subgraph := subgraphs_by_id.get(embedding.owner_id)) is not None
            ),
            key=lambda hit: hit.score,
            reverse=True,
        )
    )
    node_scores: dict[str, float] = {}
    for hit in hits:
        for node_id in hit.node_ids:
            node_scores[node_id] = max(node_scores.get(node_id, -1.0), hit.score)
    return hits, node_scores


def _estimated_tokens(node: GraphNode) -> int:
    text = node.text or node.label
    return (len(text.encode()) + 3) // 4


def _neighbor_id(edge: GraphEdge, node_id: str) -> str | None:
    if edge.type is EdgeType.USES_VALUE_FROM or edge.type is EdgeType.REFERENCES:
        return edge.target_id if edge.source_id == node_id else None
    if edge.type in _HIERARCHY_EDGES:
        return edge.source_id if edge.target_id == node_id else None
    if edge.type is EdgeType.SAME_SHEET:
        if edge.source_id == node_id:
            return edge.target_id
        if edge.target_id == node_id:
            return edge.source_id
    return None


def expand_ranked_nodes(
    *,
    ranked_node_ids: tuple[str, ...],
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
    max_nodes: int,
    max_tokens: int,
    source_evidence_node_ids: tuple[str, ...] = (),
    max_hops: int = 1,
    include_hops: bool = False,
) -> tuple[tuple[str, ...], int] | tuple[tuple[str, ...], int, int]:
    nodes_by_id = {node.id: node for node in nodes}
    selected: list[str] = []
    selected_set: set[str] = set()
    seed_set = set(ranked_node_ids)
    estimated_tokens = 0

    def add(node_id: str) -> bool:
        nonlocal estimated_tokens
        node = nodes_by_id.get(node_id)
        if node is None or node_id in selected_set or len(selected) >= max_nodes:
            return False
        node_tokens = _estimated_tokens(node)
        if estimated_tokens + node_tokens > max_tokens:
            return False
        selected.append(node_id)
        selected_set.add(node_id)
        estimated_tokens += node_tokens
        return True

    for node_id in ranked_node_ids:
        add(node_id)

    def expand(frontier: tuple[str, ...]) -> tuple[str, ...]:
        next_frontier: list[str] = []
        for edge_type in _RELATION_PRIORITY:
            for node_id in frontier:
                for edge in edges:
                    if edge.type is edge_type and (
                        neighbor_id := _neighbor_id(edge, node_id)
                    ) and neighbor_id not in seed_set and add(neighbor_id):
                        next_frontier.append(neighbor_id)
        return tuple(next_frontier)

    hops_used = 0
    frontier = (
        expand(tuple(node_id for node_id in ranked_node_ids if node_id in selected_set))
        if max_hops >= 1
        else ()
    )
    if frontier:
        hops_used = 1
    if (
        max_hops == 2
        and frontier
        and source_evidence_node_ids
        and not selected_set.intersection(source_evidence_node_ids)
    ):
        second_frontier = expand(frontier)
        if second_frontier:
            hops_used = 2
    result = (tuple(selected), estimated_tokens)
    if include_hops:
        return (*result, hops_used)
    return result


def assemble_retrieved_context(
    *,
    document_id: str,
    question: str,
    hits: tuple[RetrievalHit, ...],
    node_scores: dict[str, float],
    selected_node_ids: tuple[str, ...],
    evidence: tuple[EvidenceRecord, ...],
    hops_used: int,
    estimated_tokens: int,
) -> RetrievedContext:
    evidence_ids = tuple(
        record.id
        for node_id in selected_node_ids
        for record in evidence
        if record.node_id == node_id and not record.generated
    )
    return RetrievedContext(
        document_id=document_id,
        question=question,
        hits=hits,
        node_scores=node_scores,
        selected_node_ids=selected_node_ids,
        evidence_ids=evidence_ids,
        hops_used=hops_used,
        estimated_tokens=estimated_tokens,
    )

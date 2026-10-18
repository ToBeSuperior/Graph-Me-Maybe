from graphme.domain.enums import EdgeType, SubgraphTemplate
from graphme.domain.ids import canonical_json, content_id, sha256_bytes
from graphme.domain.models import GraphEdge, GraphNode, SubgraphRecord


def build_hierarchy_subgraph(
    *,
    seed: GraphNode,
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
) -> SubgraphRecord:
    nodes_by_id = {node.id: node for node in nodes}
    parent_by_child: dict[str, GraphEdge] = {}
    outline_by_child: dict[str, GraphEdge] = {}
    for edge in edges:
        if edge.target_id not in nodes_by_id or edge.source_id not in nodes_by_id:
            continue
        if edge.type is EdgeType.CONTAINS:
            if edge.target_id in parent_by_child:
                raise ValueError(f"node {edge.target_id} has multiple CONTAINS parents")
            parent_by_child[edge.target_id] = edge
        elif edge.type is EdgeType.OUTLINE_PARENT:
            outline_by_child.setdefault(edge.target_id, edge)
    # Outline projections enrich retrieval context without changing canonical
    # ownership. Fall back to CONTAINS when no accepted outline parent exists.
    traversal_parent = dict(parent_by_child)
    traversal_parent.update(outline_by_child)

    node_ids_list = [seed.id]
    edge_ids_list: list[str] = []
    seen = {seed.id}
    current_id = seed.id
    while (parent_edge := traversal_parent.get(current_id)) is not None:
        parent_id = parent_edge.source_id
        if parent_id in seen:
            raise ValueError("CONTAINS hierarchy contains a cycle")
        edge_ids_list.append(parent_edge.id)
        node_ids_list.append(parent_id)
        seen.add(parent_id)
        current_id = parent_id
    node_ids = tuple(node_ids_list)
    edge_ids = tuple(edge_ids_list)
    representation = canonical_json(
        {"edge_ids": edge_ids, "node_ids": node_ids, "template": SubgraphTemplate.HIERARCHY}
    )
    representation_hash = sha256_bytes(representation.encode())
    text = "\n".join(
        filter(
            None,
            (
                nodes_by_id[node_id].text or nodes_by_id[node_id].label
                for node_id in node_ids
            ),
        )
    )
    return SubgraphRecord(
        id=content_id("subgraph", representation_hash),
        document_id=seed.document_id,
        template=SubgraphTemplate.HIERARCHY,
        seed_node_id=seed.id,
        node_ids=node_ids,
        edge_ids=edge_ids,
        representation_hash=representation_hash,
        estimated_tokens=(len(text.encode()) + 3) // 4,
    )


def build_formula_dependency_subgraph(
    *,
    seed: GraphNode,
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
) -> SubgraphRecord:
    nodes_by_id = {node.id: node for node in nodes}
    dependency_edges = tuple(
        edge
        for edge in edges
        if edge.type is EdgeType.USES_VALUE_FROM
        and edge.source_id == seed.id
        and edge.target_id in nodes_by_id
    )
    node_ids = (seed.id, *(edge.target_id for edge in dependency_edges))
    edge_ids = tuple(edge.id for edge in dependency_edges)
    representation = canonical_json(
        {
            "edge_ids": edge_ids,
            "node_ids": node_ids,
            "template": SubgraphTemplate.FORMULA_DEPENDENCY,
        }
    )
    representation_hash = sha256_bytes(representation.encode())
    text = "\n".join(
        filter(
            None,
            (
                nodes_by_id[node_id].text or nodes_by_id[node_id].label
                for node_id in node_ids
            ),
        )
    )
    return SubgraphRecord(
        id=content_id("subgraph", representation_hash),
        document_id=seed.document_id,
        template=SubgraphTemplate.FORMULA_DEPENDENCY,
        seed_node_id=seed.id,
        node_ids=node_ids,
        edge_ids=edge_ids,
        representation_hash=representation_hash,
        estimated_tokens=(len(text.encode()) + 3) // 4,
    )


def build_visual_reference_subgraph(
    *,
    seed: GraphNode,
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
) -> SubgraphRecord:
    nodes_by_id = {node.id: node for node in nodes}
    reference_edges = tuple(
        edge
        for edge in edges
        if edge.type is EdgeType.REFERENCES
        and edge.source_id == seed.id
        and edge.target_id in nodes_by_id
    )
    caption_edges = tuple(
        edge
        for edge in edges
        if edge.type is EdgeType.CAPTION_OF
        and edge.target_id == seed.id
        and edge.source_id in nodes_by_id
    )
    node_ids = (
        seed.id,
        *(edge.target_id for edge in reference_edges),
        *(edge.source_id for edge in caption_edges),
    )
    edge_ids = tuple(edge.id for edge in (*reference_edges, *caption_edges))
    representation = canonical_json(
        {
            "edge_ids": edge_ids,
            "node_ids": node_ids,
            "template": SubgraphTemplate.VISUAL_REFERENCE,
        }
    )
    representation_hash = sha256_bytes(representation.encode())
    text = "\n".join(
        filter(
            None,
            (
                nodes_by_id[node_id].text or nodes_by_id[node_id].label
                for node_id in node_ids
            ),
        )
    )
    return SubgraphRecord(
        id=content_id("subgraph", representation_hash),
        document_id=seed.document_id,
        template=SubgraphTemplate.VISUAL_REFERENCE,
        seed_node_id=seed.id,
        node_ids=node_ids,
        edge_ids=edge_ids,
        representation_hash=representation_hash,
        estimated_tokens=(len(text.encode()) + 3) // 4,
    )

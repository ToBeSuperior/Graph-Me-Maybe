"""The edges that leave this system, in the four types anything outside it reads.

Inside, what holds a node and what it reads under are two different facts: `CONTAINS` is
the file's own nesting and is the same on every run, `OUTLINE_PARENT` is what the model
read off the page and can be re-read. A reader of the finished graph asks neither -- it
asks who the parent is -- so both leave as one `PARENT_OF` from parent to child, one per
node, with `decided_by` saying which answered. `USES_VALUE_FROM` is a reference a formula
makes, so it leaves as `REFERENCES` with its own kind kept. What is left is what nothing
outside reads: a sheet two nodes happen to share says nothing about either one.
"""
from __future__ import annotations

from graphme.domain.enums import EdgeType, NodeType
from graphme.domain.models import ExtractedDocument, GraphEdge


def final_edges(extracted: ExtractedDocument) -> tuple[GraphEdge, ...]:
    """The document's edges as the four published types, in their original order."""
    outline = {
        edge.target_id: edge
        for edge in extracted.edges
        if edge.type is EdgeType.OUTLINE_PARENT
    }
    # A caption is not a part of the document; it is something said about one of its parts,
    # and it hangs off that part by the edge that says so. Inside, it needs a holder like
    # every other node, but that holder is not a place in the outline.
    beside = {
        node.id
        for node in extracted.nodes
        if node.type is NodeType.VLM_ENRICHED_CAPTION
    }
    published: list[GraphEdge] = []
    for edge in extracted.edges:
        if edge.type is EdgeType.OUTLINE_PARENT:
            continue  # published where the node's own CONTAINS edge is
        if edge.type is EdgeType.CONTAINS:
            if edge.target_id in beside:
                continue
            # The outline wins where it read something; the file answers for the rest.
            chosen = outline.get(edge.target_id, edge)
            read = chosen.type is EdgeType.OUTLINE_PARENT
            published.append(
                chosen.model_copy(
                    update={
                        "type": EdgeType.PARENT_OF,
                        "metadata": {
                            **chosen.metadata,
                            "decided_by": "outline" if read else "file",
                            # What the file holds this node in stays readable: a picture in a
                            # table cell is part of that table even when it reads under a
                            # heading somewhere else.
                            **({"file_parent_id": edge.source_id} if read else {}),
                        },
                    }
                )
            )
        elif edge.type in (EdgeType.REFERENCES, EdgeType.USES_VALUE_FROM):
            published.append(
                edge.model_copy(
                    update={
                        "type": EdgeType.REFERENCES,
                        "metadata": {**edge.metadata, "reference_kind": edge.type.value},
                    }
                )
            )
        elif edge.type in (EdgeType.NEXT, EdgeType.CAPTION_OF):
            published.append(edge)
    return tuple(published)

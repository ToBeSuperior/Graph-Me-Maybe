"""A document read on its own, cut into the passages the graph space would cut it into.

For a reader who keeps passages in a store of their own: `graphme.passages(graphme.read(path))`.
The units are taken from the same records the space is written from (`graph_records`) by the
same rule its query reads them back with (`neo4j_space._UNITS`), and cut by the one rule
(`graph_chunks.chunks`) -- so a passage here and one in the app are the same text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graphme.adapters.export.neo4j import graph_records
from graphme.adapters.storage.neo4j_space import unit_order
from graphme.application.graph_chunks import PATH_LABEL_CHARS, GraphUnit, chunks
from graphme.domain.enums import EdgeType, NodeType
from graphme.domain.models import ExtractedDocument

_NOT_UNITS = {NodeType.DOCUMENT.value, NodeType.EXTERNAL_DOCUMENT.value,
              NodeType.VLM_ENRICHED_CAPTION.value}


@dataclass(frozen=True)
class Passage:
    """One passage: its text, where it is from, and the units it holds (node ids)."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    """`file`, `format`, `headings` (the outline above it), and where it is in the file's own
    terms when the file says: `sheet` and `cells`, `slide`, `pages`."""
    node_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "metadata": dict(self.metadata),
                "node_ids": list(self.node_ids)}


def passages(document: ExtractedDocument) -> list[Passage]:
    """`document` cut as the graph space cuts it when it is embedded."""
    records = graph_records(document)
    nodes = {row["id"]: row["properties"] for row in records["nodes"]}
    parents: dict[str, str] = {}
    captions: dict[str, list[str]] = {}
    for row in records["relationships"]:
        if row["type"] == EdgeType.PARENT_OF.value:
            parents[row["end"]] = row["start"]
        elif row["type"] == EdgeType.CAPTION_OF.value:
            captions.setdefault(row["end"], []).append(row["start"])
    holders = set(parents.values())

    units = []
    for node_id, node in nodes.items():
        if node["type"] in _NOT_UNITS or node_id not in parents:
            continue
        parent = parents[node_id]
        said = " ".join(nodes[c].get("text", "") for c in captions.get(node_id, ())).strip()
        text = node.get("text")
        if text is not None and said:
            text = f"{text}\n{said}"
        elif text is None:
            text = said or (node.get("label", "") if node_id in holders else "")
        bbox = node.get("bbox")
        units.append(GraphUnit(
            id=node_id,
            parent=None if nodes[parent]["type"] == NodeType.DOCUMENT.value else parent,
            text=text,
            order=unit_order([
                _first(node.get("page"), node.get("slide"), 0),
                _first(node.get("meta_reading_order"), node.get("meta_source_order"),
                       node.get("meta_body_index"), bbox[1] if bbox else None, 0)]),
            attached=tuple(captions.get(node_id, ())),
            quote_of=(node.get("meta_mail_quote_of") or "") if node.get("meta_mail_quoted")
            else None,
        ))

    root = nodes.get(document.document.id, {})
    title = root.get("label") or document.document.id
    by_id = {unit.id: unit for unit in units}
    return [Passage(text=chunk.text,
                    metadata=_where(document, [nodes[i] for i in chunk.member_ids if i in nodes],
                                    _headings(chunk.member_ids, by_id)),
                    node_ids=chunk.member_ids)
            for chunk in chunks(title, units)]


def _first(*values: Any) -> Any:
    """Cypher's `coalesce`: the first value that is there."""
    return next(value for value in values if value is not None)


def _headings(members: tuple[str, ...], by_id: dict[str, GraphUnit]) -> list[str]:
    """The outline above a passage's first unit, outermost first, each its first line."""
    path: list[str] = []
    unit = by_id.get(members[0]) if members else None
    while unit is not None and unit.parent is not None and unit.parent not in members:
        unit = by_id.get(unit.parent)
        if unit is not None:
            path.append(unit.text.split("\n", 1)[0][:PATH_LABEL_CHARS])
    return path[::-1]


def _where(document: ExtractedDocument, members: list[dict[str, Any]],
           headings: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"file": document.document.file_name,
                                "format": document.document.format.value,
                                "headings": headings}
    sheets = [m["sheet"] for m in members if m.get("sheet")]
    if sheets:
        metadata["sheet"] = sheets[0]
        metadata["cells"] = [m["cell_range"] for m in members if m.get("cell_range")]
    if slides := sorted({m["slide"] for m in members if m.get("slide") is not None}):
        metadata["slide"] = slides[0]
    if pages := sorted({m["page"] for m in members if m.get("page") is not None}):
        metadata["pages"] = pages
    return metadata

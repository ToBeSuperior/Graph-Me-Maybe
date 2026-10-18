from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from graphme.application.subgraphs import (
    build_formula_dependency_subgraph,
    build_hierarchy_subgraph,
    build_visual_reference_subgraph,
)
from graphme.domain.enums import (
    EdgeType,
    EmbeddingInputType,
    EvidenceKind,
    NodeType,
)
from graphme.domain.ids import canonical_json, content_id, sha256_bytes
from graphme.domain.models import (
    EmbeddingPart,
    EmbeddingRequest,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    ImagePart,
    SubgraphRecord,
    TextPart,
)
from graphme.ports.storage import AssetStore

_VISUAL_TYPES = frozenset({NodeType.CHART, NodeType.IMAGE, NodeType.TABLE})


@dataclass(frozen=True, slots=True)
class ComposedSubgraph:
    subgraph: SubgraphRecord
    request: EmbeddingRequest
    evidence: tuple[EvidenceRecord, ...]


class MultimodalIndexComposer:
    """Compose fixed graph contexts into exact, crop-grounded embedding inputs."""

    def __init__(self, *, asset_store: AssetStore) -> None:
        self._asset_store = asset_store

    def compose(
        self, extracted: ExtractedDocument
    ) -> tuple[ComposedSubgraph, ...]:
        unique: dict[str, ComposedSubgraph] = {}
        for candidate in self._candidates(extracted):
            item = self._compose(extracted, candidate)
            unique.setdefault(item.subgraph.id, item)
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.subgraph.template.value,
                    item.subgraph.seed_node_id,
                    item.subgraph.id,
                ),
            )
        )

    @staticmethod
    def _candidates(extracted: ExtractedDocument) -> tuple[SubgraphRecord, ...]:
        retrievable = tuple(
            sorted(
                (node for node in extracted.nodes if node.retrievable),
                key=MultimodalIndexComposer._node_sort_key,
            )
        )
        candidates: list[SubgraphRecord] = []
        for seed in retrievable:
            candidates.append(
                build_hierarchy_subgraph(
                    seed=seed,
                    nodes=extracted.nodes,
                    edges=extracted.edges,
                )
            )
            if any(
                edge.type is EdgeType.USES_VALUE_FROM
                and edge.source_id == seed.id
                for edge in extracted.edges
            ):
                candidates.append(
                    build_formula_dependency_subgraph(
                        seed=seed,
                        nodes=extracted.nodes,
                        edges=extracted.edges,
                    )
                )
            if seed.type in _VISUAL_TYPES and any(
                (
                    edge.type is EdgeType.REFERENCES
                    and edge.source_id == seed.id
                )
                or (
                    edge.type is EdgeType.CAPTION_OF
                    and edge.target_id == seed.id
                )
                for edge in extracted.edges
            ):
                candidates.append(
                    build_visual_reference_subgraph(
                        seed=seed,
                        nodes=extracted.nodes,
                        edges=extracted.edges,
                    )
                )
        return tuple(candidates)

    def _compose(
        self,
        extracted: ExtractedDocument,
        membership: SubgraphRecord,
    ) -> ComposedSubgraph:
        nodes_by_id = {node.id: node for node in extracted.nodes}
        edges_by_id = {edge.id: edge for edge in extracted.edges}
        nodes = tuple(nodes_by_id[node_id] for node_id in membership.node_ids)
        edges = tuple(edges_by_id[edge_id] for edge_id in membership.edge_ids)
        node_ids = set(membership.node_ids)
        evidence = tuple(
            sorted(
                (
                    record
                    for record in extracted.evidence
                    if record.node_id in node_ids
                ),
                key=lambda record: (record.node_id, record.generated, record.id),
            )
        )
        source_by_node = self._source_evidence(nodes, evidence)
        visuals_by_node = self._source_visuals(nodes, source_by_node)
        text = self._canonical_text(membership, nodes, edges, evidence, source_by_node)
        image_parts = tuple(
            self._image_part(nodes_by_id[node_id], record)
            for node_id, record in visuals_by_node.items()
        )
        parts: tuple[EmbeddingPart, ...] = (
            TextPart(text=text),
            *self._unique_images(image_parts),
        )
        representation_hash = sha256_bytes(
            canonical_json(
                [part.model_dump(mode="json") for part in parts]
            ).encode()
        )
        subgraph_id = content_id("subgraph", representation_hash)
        estimated_tokens = (len(text.encode()) + 3) // 4 + sum(
            (part.width * part.height + 559) // 560
            for part in image_parts
            if part.width is not None and part.height is not None
        )
        subgraph = membership.model_copy(
            update={
                "id": subgraph_id,
                "representation_hash": representation_hash,
                "estimated_tokens": estimated_tokens,
            }
        )
        request = EmbeddingRequest(
            id=subgraph_id,
            input_type=EmbeddingInputType.DOCUMENT,
            parts=parts,
            representation_hash=representation_hash,
        )
        return ComposedSubgraph(
            subgraph=subgraph,
            request=request,
            evidence=evidence,
        )

    @staticmethod
    def _source_evidence(
        nodes: tuple[GraphNode, ...],
        evidence: tuple[EvidenceRecord, ...],
    ) -> dict[str, tuple[EvidenceRecord, ...]]:
        result: dict[str, tuple[EvidenceRecord, ...]] = {}
        for node in nodes:
            if not node.retrievable:
                continue
            matches = tuple(
                record
                for record in evidence
                if record.node_id == node.id and not record.generated
            )
            if not matches:
                raise ValueError(f"retrievable node {node.id} must have source evidence")
            result[node.id] = matches
        return result

    @staticmethod
    def _source_visuals(
        nodes: tuple[GraphNode, ...],
        source_by_node: dict[str, tuple[EvidenceRecord, ...]],
    ) -> dict[str, EvidenceRecord]:
        result: dict[str, EvidenceRecord] = {}
        for node in nodes:
            if not node.retrievable:
                continue
            assets = tuple(
                record
                for record in source_by_node[node.id]
                if record.asset_hash is not None
                and record.mime_type is not None
                and record.mime_type.lower()
                not in {"image/wmf", "image/x-wmf", "image/emf", "image/x-emf"}
            )
            if "semantic_unit_kind" in node.metadata:
                if len(assets) != 1:
                    raise ValueError(
                        f"retrievable node {node.id} must have exactly one source crop"
                    )
                result[node.id] = assets[0]
                continue
            visual_observations = tuple(
                record
                for record in assets
                if isinstance(record.data, dict)
                and record.data.get("visual_observation") is True
            )
            previews = tuple(
                record
                for record in assets
                if record.kind is EvidenceKind.RENDERED_PREVIEW
            )
            source_media = tuple(
                record
                for record in assets
                if record.kind is EvidenceKind.SOURCE_MEDIA
            )
            designated = visual_observations or previews or source_media
            if designated:
                result[node.id] = designated[0]
        return result

    def _image_part(self, node: GraphNode, record: EvidenceRecord) -> ImagePart:
        metadata = record.locator.metadata
        asset_hash = record.asset_hash
        if asset_hash is None or record.mime_type is None:
            raise ValueError("source visual evidence must identify an image asset")
        if "semantic_unit_kind" in node.metadata:
            crop_hash = metadata.get("crop_asset_hash")
            width = self._as_positive_int(metadata.get("crop_width"))
            height = self._as_positive_int(metadata.get("crop_height"))
            pixel_bbox = metadata.get("pixel_bbox")
            if (
                crop_hash != asset_hash
                or width is None
                or height is None
                or not isinstance(pixel_bbox, list)
                or len(pixel_bbox) != 4
            ):
                raise ValueError("source crop evidence has invalid crop metadata")
            if record.mime_type != "image/png":
                raise ValueError("source crop evidence must use image/png")
        else:
            record_data = record.data if isinstance(record.data, dict) else {}
            width = self._as_positive_int(
                metadata.get("crop_width")
                or record_data.get("crop_width")
                or node.metadata.get("width")
            )
            height = self._as_positive_int(
                metadata.get("crop_height")
                or record_data.get("crop_height")
                or node.metadata.get("height")
            )
        suffix = self._image_suffix(record.mime_type)
        content = self._asset_store.read(asset_hash, suffix=suffix)
        if not content or sha256_bytes(content) != asset_hash:
            raise ValueError("source visual evidence asset does not match its hash")
        if width is None or height is None:
            try:
                pixmap = pymupdf.Pixmap(content)  # type: ignore[no-untyped-call]
            except RuntimeError:
                # Preserve the native source evidence even when PyMuPDF cannot decode
                # the Office media format; it must not block the text/graph index.
                return ImagePart(
                    asset_hash=asset_hash,
                    mime_type=record.mime_type,
                    width=None,
                    height=None,
                    size_bytes=len(content),
                )
            width = pixmap.width
            height = pixmap.height
        return ImagePart(
            asset_hash=asset_hash,
            mime_type=record.mime_type,
            width=width,
            height=height,
            size_bytes=len(content),
        )

    @staticmethod
    def _image_suffix(mime_type: str) -> str:
        if not mime_type.startswith("image/"):
            raise ValueError("source visual evidence must use an image MIME type")
        subtype = mime_type.removeprefix("image/").lower()
        aliases = {
            "jpeg": ".jpeg",
            "x-wmf": ".wmf",
            "x-emf": ".emf",
        }
        return aliases.get(subtype, f".{subtype}")

    @staticmethod
    def _canonical_text(
        membership: SubgraphRecord,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
        evidence: tuple[EvidenceRecord, ...],
        source_by_node: dict[str, tuple[EvidenceRecord, ...]],
    ) -> str:
        generated_by_node: dict[str, list[dict[str, object]]] = {}
        for record in evidence:
            if not record.generated:
                continue
            generated_by_node.setdefault(record.node_id, []).append(
                {
                    "data": record.data,
                    "evidence_id": record.id,
                    "kind": record.kind.value,
                    "text": record.text,
                }
            )
        payload = {
            "edges": [
                {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "type": edge.type.value,
                }
                for edge in edges
            ],
            "nodes": [
                {
                    "generated_interpretation": {
                        "evidence": generated_by_node.get(node.id, []),
                        "text": node.text,
                    },
                    "id": node.id,
                    "label": node.label,
                    "locator": node.locator.model_dump(mode="json"),
                    "source_authority": [
                        {
                            "asset_hash": record.asset_hash,
                            "data": record.data,
                            "evidence_id": record.id,
                            "kind": record.kind.value,
                            "mime_type": record.mime_type,
                            "text": record.text,
                        }
                        for record in source_by_node.get(node.id, ())
                    ],
                    "type": node.type.value,
                }
                for node in nodes
            ],
            "seed_node_id": membership.seed_node_id,
            "template": membership.template.value,
        }
        return canonical_json(payload)

    @staticmethod
    def _unique_images(parts: tuple[ImagePart, ...]) -> tuple[ImagePart, ...]:
        unique: dict[str, ImagePart] = {}
        for part in parts:
            unique.setdefault(part.asset_hash, part)
        return tuple(unique.values())

    @staticmethod
    def _as_positive_int(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    @staticmethod
    def _node_sort_key(node: GraphNode) -> tuple[int, int, str]:
        reading_order = node.metadata.get("reading_order")
        if not isinstance(reading_order, int) or isinstance(reading_order, bool):
            reading_order = 0
        return node.locator.page or 0, reading_order, node.id

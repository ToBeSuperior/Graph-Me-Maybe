from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf

from graphme.adapters.rendering.page_image import (
    PageImage,
    PageImageRenderResult,
    SourcePageMapping,
)
from graphme.domain.enums import (
    DocumentFormat,
    EdgeType,
    EvidenceKind,
    NodeType,
    SemanticUnitKind,
)
from graphme.domain.ids import canonical_json, sha256_file, stable_id
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    JsonValue,
    PageSegmentationRequest,
    SegmentedUnit,
    SourceLocator,
    native_edge,
)
from graphme.ports.storage import AssetStore
from graphme.ports.vision import VisionProvider


class MappedPageRenderer(Protocol):
    def render_with_mapping(
        self, source: Path, document_format: DocumentFormat
    ) -> PageImageRenderResult: ...


_UNIT_NODE_TYPES: dict[SemanticUnitKind, NodeType] = {
    SemanticUnitKind.TABLE: NodeType.TABLE,
    SemanticUnitKind.BODY_TEXT: NodeType.TEXT_CHUNK,
    SemanticUnitKind.MEMO: NodeType.TEXT_CHUNK,
    SemanticUnitKind.FIGURE: NodeType.IMAGE,
    # Text is text however the page presents it; the kind the model saw is recorded in
    # the node's metadata, not promoted into a type nothing else produces.
    SemanticUnitKind.CAPTION: NodeType.TEXT_CHUNK,
    SemanticUnitKind.HEADING: NodeType.TEXT_CHUNK,
}


@dataclass(frozen=True, slots=True)
class VisualObservation:
    """One validated, source-mapped rendered observation with its exact crop."""

    unit: SegmentedUnit
    locator: SourceLocator
    page_asset_hash: str
    crop_asset_hash: str
    crop_width: int
    crop_height: int


class GroundedDocumentBuilder:
    """Turn trusted rendered pages and VLM units into a grounded graph."""

    def __init__(
        self,
        *,
        renderer: MappedPageRenderer,
        asset_store: AssetStore,
        vision_provider: VisionProvider,
    ) -> None:
        self._renderer = renderer
        self._asset_store = asset_store
        self._vision_provider = vision_provider

    def observe(
        self, *, source: Path, document_format: DocumentFormat
    ) -> tuple[VisualObservation, ...]:
        """Collect validated rendered observations without creating semantic nodes."""
        rendered = self._renderer.render_with_mapping(source, document_format)
        pages = {page.page_index: page for page in rendered.pages}
        if len(pages) != len(rendered.pages) or not pages:
            raise ValueError("rendered pages must have unique indexes")
        mapping_by_page = self._mapping_by_page(rendered)
        if set(mapping_by_page) != set(pages):
            raise ValueError("every rendered page must have exactly one source mapping")

        observations: list[VisualObservation] = []
        for page_index in sorted(pages):
            page = pages[page_index]
            mapping = mapping_by_page[page_index]
            image = self._asset_store.read(page.asset_hash, suffix=".png")
            actual = pymupdf.Pixmap(image)  # type: ignore[no-untyped-call]
            if actual.width != page.width or actual.height != page.height:
                raise ValueError(
                    f"page {page_index} asset dimensions do not match rendered metadata"
                )
            request = PageSegmentationRequest(
                page_index=page_index,
                width=page.width,
                height=page.height,
                asset_hash=page.asset_hash,
                mime_type=page.mime_type,
                locator=mapping.locator.model_copy(update={"page": page_index}),
            )
            result = self._vision_provider.segment_page(request=request, image=image)
            result.validate_for(request)
            for unit in sorted(result.units, key=lambda item: item.reading_order):
                locator, crop_hash, crop_width, crop_height = self._crop_unit(
                    unit=unit,
                    mapping_locator=request.locator,
                    pixmap=actual,
                )
                observations.append(
                    VisualObservation(
                        unit=unit,
                        locator=locator,
                        page_asset_hash=page.asset_hash,
                        crop_asset_hash=crop_hash,
                        crop_width=crop_width,
                        crop_height=crop_height,
                    )
                )
        return tuple(observations)

    def build(
        self, *, source: Path, document_format: DocumentFormat
    ) -> ExtractedDocument:
        content_hash = sha256_file(source)
        rendered = self._renderer.render_with_mapping(source, document_format)
        pages = {page.page_index: page for page in rendered.pages}
        if len(pages) != len(rendered.pages) or not pages:
            raise ValueError("rendered pages must have unique indexes")
        mapping_by_page = self._mapping_by_page(rendered)
        if set(mapping_by_page) != set(pages):
            raise ValueError("every rendered page must have exactly one source mapping")

        document_id = stable_id(content_hash, f"document:file={source.name}")
        document_locator = SourceLocator(file_name=source.name, label=source.name)
        # Which engine drew the page, so an observation can be traced to its rendering.
        render_provenance: dict[str, JsonValue] = {
            "renderer": "pymupdf" if document_format is DocumentFormat.PDF else "ooxml-browser",
            "source_suffix": source.suffix.lower(),
        }
        nodes: list[GraphNode] = [
            GraphNode(
                id=document_id,
                document_id=document_id,
                type=NodeType.DOCUMENT,
                label=source.name,
                locator=document_locator,
                content_hash=content_hash,
                retrievable=False,
            )
        ]
        edges: list[GraphEdge] = []
        evidence: list[EvidenceRecord] = [
            EvidenceRecord(
                id=stable_id(content_hash, "evidence:document"),
                document_id=document_id,
                node_id=document_id,
                kind=EvidenceKind.SOURCE_METADATA,
                locator=document_locator,
                text=f"document {source.name}",
                data={"rendering": render_provenance} if render_provenance else {},
            )
        ]
        ordered_children: dict[str, list[str]] = {document_id: []}
        containers: dict[tuple[int, int, str], str] = {}

        for page_index in sorted(pages):
            page = pages[page_index]
            mapping = mapping_by_page[page_index]
            container_key = (
                mapping.page_start,
                mapping.page_end,
                canonical_json(mapping.locator.model_dump(mode="json")),
            )
            container_id = containers.get(container_key)
            if container_id is None:
                container_type = self._container_type(document_format, mapping.locator)
                container_id = stable_id(
                    content_hash,
                    f"container:{container_type.value}:pages={mapping.page_start}-{mapping.page_end}"
                    f":label={mapping.locator.label}",
                )
                containers[container_key] = container_id
                container_locator = mapping.locator
                nodes.append(
                    GraphNode(
                        id=container_id,
                        document_id=document_id,
                        type=container_type,
                        label=mapping.locator.label,
                        locator=container_locator,
                        retrievable=False,
                    )
                )
                edges.append(
                    native_edge(
                        content_hash,
                        document_id,
                        document_id,
                        container_id,
                        EdgeType.CONTAINS,
                    )
                )
                ordered_children[document_id].append(container_id)
                ordered_children[container_id] = []

            image = self._asset_store.read(page.asset_hash, suffix=".png")
            actual = pymupdf.Pixmap(image)  # type: ignore[no-untyped-call]
            if actual.width != page.width or actual.height != page.height:
                raise ValueError(
                    f"page {page_index} asset dimensions do not match rendered metadata"
                )
            request = PageSegmentationRequest(
                page_index=page_index,
                width=page.width,
                height=page.height,
                asset_hash=page.asset_hash,
                mime_type=page.mime_type,
                locator=mapping.locator.model_copy(update={"page": page_index}),
            )
            result = self._vision_provider.segment_page(request=request, image=image)
            result.validate_for(request)
            for unit in sorted(result.units, key=lambda item: item.reading_order):
                node, source_record, generated_record = self._ground_unit(
                    unit=unit,
                    page=page,
                    mapping_locator=request.locator,
                    content_hash=content_hash,
                    document_id=document_id,
                    container_id=container_id,
                    pixmap=actual,
                    render_provenance=render_provenance,
                )
                nodes.append(node)
                evidence.extend((source_record, generated_record))
                edges.append(
                    native_edge(
                        content_hash,
                        document_id,
                        container_id,
                        node.id,
                        EdgeType.CONTAINS,
                    )
                )
                ordered_children[container_id].append(node.id)

        extracted = ExtractedDocument(
            document=DocumentRecord(
                id=document_id,
                content_hash=content_hash,
                file_name=source.name,
                format=document_format,
                source_path=source,
                size_bytes=source.stat().st_size,
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
            evidence=tuple(evidence),
        )
        return extracted.validate_canonical_semantic_graph()

    @staticmethod
    def _mapping_by_page(
        rendered: PageImageRenderResult,
    ) -> dict[int, SourcePageMapping]:
        result: dict[int, SourcePageMapping] = {}
        for mapping in rendered.mappings:
            for page_index in range(mapping.page_start, mapping.page_end + 1):
                if page_index in result:
                    raise ValueError(
                        "every rendered page must have exactly one source mapping"
                    )
                result[page_index] = mapping
        return result

    @staticmethod
    def _container_type(document_format: DocumentFormat, locator: SourceLocator) -> NodeType:
        if document_format is DocumentFormat.PPTX or locator.slide is not None:
            return NodeType.SLIDE
        if document_format is DocumentFormat.XLSX or locator.sheet is not None:
            return NodeType.WORKSHEET
        return NodeType.SECTION

    def _crop_unit(
        self,
        *,
        unit: SegmentedUnit,
        mapping_locator: SourceLocator,
        pixmap: pymupdf.Pixmap,
    ) -> tuple[SourceLocator, str, int, int]:
        x0, y0, x1, y1 = unit.bbox.as_tuple()
        left = max(0, min(pixmap.width - 1, math.floor(x0 * pixmap.width)))
        top = max(0, min(pixmap.height - 1, math.floor(y0 * pixmap.height)))
        right = max(left + 1, min(pixmap.width, math.ceil(x1 * pixmap.width)))
        bottom = max(top + 1, min(pixmap.height, math.ceil(y1 * pixmap.height)))
        if right <= left or bottom <= top:
            raise ValueError("segmented unit bbox produced an empty crop")
        crop = pymupdf.Pixmap(  # type: ignore[no-untyped-call]
            pixmap.colorspace,
            (0, 0, right - left, bottom - top),
            pixmap.alpha,
        )
        crop.copy(  # type: ignore[no-untyped-call]
            pixmap,
            pymupdf.IRect(left, top, right, bottom),  # type: ignore[no-untyped-call]
        )
        crop_png = crop.tobytes("png")  # type: ignore[no-untyped-call]
        if not crop_png:
            raise ValueError("segmented unit bbox produced an empty crop")
        crop_hash = self._asset_store.put(crop_png, suffix=".png")
        locator = mapping_locator.model_copy(
            update={
                "bbox": unit.bbox.as_tuple(),
                "metadata": {
                    **mapping_locator.metadata,
                    "pixel_bbox": [left, top, right, bottom],
                    "crop_asset_hash": crop_hash,
                    "crop_width": right - left,
                    "crop_height": bottom - top,
                },
            }
        )
        return locator, crop_hash, right - left, bottom - top

    def _ground_unit(
        self,
        *,
        unit: SegmentedUnit,
        page: PageImage,
        mapping_locator: SourceLocator,
        content_hash: str,
        document_id: str,
        container_id: str,
        pixmap: pymupdf.Pixmap,
        render_provenance: dict[str, JsonValue] | None,
    ) -> tuple[GraphNode, EvidenceRecord, EvidenceRecord]:
        # The identity intentionally excludes floating point bbox values.
        node_id = stable_id(
            content_hash,
            f"unit:page={mapping_locator.page}:kind={unit.kind.value}:"
            f"reading_order={unit.reading_order}",
        )
        locator, crop_hash, crop_width, crop_height = self._crop_unit(
            unit=unit,
            mapping_locator=mapping_locator,
            pixmap=pixmap,
        )
        pixel_bbox = locator.metadata["pixel_bbox"]
        interpretation = unit.interpreted_text
        if interpretation is None and unit.structured_data is not None:
            interpretation = canonical_json(unit.structured_data)
        metadata: dict[str, JsonValue] = {
            "semantic_unit_kind": unit.kind.value,
            "reading_order": unit.reading_order,
            "confidence": unit.confidence,
            "crop_asset_hash": crop_hash,
            "crop_width": crop_width,
            "crop_height": crop_height,
            "pixel_bbox": pixel_bbox,
            "container_id": container_id,
        }
        if unit.structured_data is not None:
            metadata["structured_data"] = unit.structured_data
        node = GraphNode(
            id=node_id,
            document_id=document_id,
            type=_UNIT_NODE_TYPES[unit.kind],
            label=unit.label,
            locator=locator,
            content_hash=crop_hash,
            text=interpretation,
            metadata=metadata,
        )
        source_kind = (
            EvidenceKind.SOURCE_MEDIA
            if unit.kind is SemanticUnitKind.FIGURE
            else EvidenceKind.RENDERED_PREVIEW
        )
        source_record = EvidenceRecord(
            id=stable_id(content_hash, f"evidence:crop:{node_id}"),
            document_id=document_id,
            node_id=node_id,
            kind=source_kind,
            locator=locator,
            asset_hash=crop_hash,
            mime_type="image/png",
            data={
                "page_asset_hash": page.asset_hash,
                **(
                    {"rendering": render_provenance}
                    if render_provenance is not None
                    else {}
                ),
            },
        )
        generated_payload: JsonValue = unit.structured_data
        if generated_payload is None:
            generated_payload = unit.interpreted_text or unit.label
        generated_record = EvidenceRecord(
            id=stable_id(content_hash, f"evidence:extraction:{node_id}"),
            document_id=document_id,
            node_id=node_id,
            kind=EvidenceKind.GENERATED_EXTRACTION,
            locator=locator,
            text=unit.interpreted_text,
            data=generated_payload,
            generated=True,
        )
        return node, source_record, generated_record

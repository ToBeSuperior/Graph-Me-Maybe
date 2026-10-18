from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from graphme.application.grounding import VisualObservation
from graphme.domain.enums import DocumentFormat, EvidenceKind, NodeType, SemanticUnitKind
from graphme.domain.ids import canonical_json, stable_id
from graphme.domain.models import (
    EvidenceRecord,
    ExtractedDocument,
    GraphNode,
    SourceLocator,
)

# Extraction calls all text text. What a rendered page shows as a heading, a caption or a
# memo is an observation about that text, so every kind of text observation lands on it.
_TEXT = frozenset({NodeType.TEXT_CHUNK})
_COMPATIBLE_TYPES: dict[SemanticUnitKind, frozenset[NodeType]] = {
    SemanticUnitKind.TABLE: frozenset({NodeType.TABLE, NodeType.PIVOT_TABLE}),
    SemanticUnitKind.BODY_TEXT: _TEXT,
    SemanticUnitKind.MEMO: _TEXT,
    SemanticUnitKind.FIGURE: frozenset({NodeType.IMAGE, NodeType.CHART}),
    SemanticUnitKind.HEADING: _TEXT,
    SemanticUnitKind.CAPTION: _TEXT,
}
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


class ReconciliationScope(StrEnum):
    DOCUMENT = "document"
    PAGE = "page"
    SLIDE = "slide"
    SHEET = "sheet"


@dataclass(frozen=True)
class ReconciliationStrategy:
    scope: ReconciliationScope
    candidate_types: frozenset[NodeType]


_DEFAULT_STRATEGIES: Mapping[DocumentFormat, ReconciliationStrategy] = MappingProxyType(
    {
        DocumentFormat.XLSX: ReconciliationStrategy(
            scope=ReconciliationScope.SHEET,
            candidate_types=frozenset(
                {
                    NodeType.TABLE,
                    NodeType.PIVOT_TABLE,
                    NodeType.TEXT_CHUNK,
                    NodeType.IMAGE,
                }
            ),
        ),
        DocumentFormat.PDF: ReconciliationStrategy(
            scope=ReconciliationScope.PAGE,
            candidate_types=frozenset(
                {
                    NodeType.TABLE,
                    NodeType.TEXT_CHUNK,
                    NodeType.IMAGE,
                }
            ),
        ),
        DocumentFormat.PPTX: ReconciliationStrategy(
            scope=ReconciliationScope.SLIDE,
            candidate_types=frozenset(
                {
                    NodeType.TABLE,
                    NodeType.TEXT_CHUNK,
                    NodeType.IMAGE,
                    NodeType.CHART,
                }
            ),
        ),
        DocumentFormat.DOCX: ReconciliationStrategy(
            scope=ReconciliationScope.DOCUMENT,
            candidate_types=frozenset(
                {
                    NodeType.TABLE,
                    NodeType.TEXT_CHUNK,
                    NodeType.IMAGE,
                }
            ),
        ),
    }
)


class SemanticReconciler:
    """Attach conservative rendered evidence to an authoritative native graph."""

    def __init__(
        self,
        *,
        strategies: Mapping[DocumentFormat, ReconciliationStrategy] | None = None,
    ) -> None:
        self._strategies = MappingProxyType(dict(strategies or _DEFAULT_STRATEGIES))

    def reconcile(
        self,
        native: ExtractedDocument,
        observations: tuple[VisualObservation, ...],
    ) -> ExtractedDocument:
        native.validate_canonical_semantic_graph()
        strategy = self._strategies.get(native.document.format)
        if strategy is None:
            return native
        scope_key = self._scope_key(strategy.scope)
        nodes_by_container: dict[object, list[GraphNode]] = defaultdict(list)
        for node in native.nodes:
            container = scope_key(node.locator)
            if container is not None and node.type in strategy.candidate_types:
                nodes_by_container[container].append(node)

        additions: list[EvidenceRecord] = []
        matched_nodes: set[str] = set()
        for observation in observations:
            container = scope_key(observation.locator)
            if container is None:
                continue
            match = self._match(
                observation,
                tuple(nodes_by_container.get(container, ())),
                matched_nodes,
            )
            if match is None:
                # Unmatched rendered units are deliberately not promoted. Native XLSX
                # structure is richer than page geometry, and generic/offline units
                # cannot prove that they represent a new semantic object.
                continue
            matched_nodes.add(match.id)
            additions.extend(self._evidence(native, match, observation))

        reconciled = native.model_copy(
            update={"evidence": (*native.evidence, *additions)}
        )
        return reconciled.validate_canonical_semantic_graph()

    @staticmethod
    def _scope_key(
        scope: ReconciliationScope,
    ) -> Callable[[SourceLocator], tuple[str, object] | None]:
        # Always include the file name so observations from another source cannot
        # match merely because a page/sheet/slide identifier happens to collide.
        def scoped(value: object | None, locator: SourceLocator) -> tuple[str, object] | None:
            return None if value is None else (locator.file_name, value)

        if scope is ReconciliationScope.SHEET:
            return lambda locator: scoped(locator.sheet, locator)
        if scope is ReconciliationScope.PAGE:
            return lambda locator: scoped(locator.page, locator)
        if scope is ReconciliationScope.SLIDE:
            return lambda locator: scoped(locator.slide, locator)
        return lambda locator: (locator.file_name, locator.file_name)

    @classmethod
    def _match(
        cls,
        observation: VisualObservation,
        candidates: tuple[GraphNode, ...],
        matched_nodes: set[str],
    ) -> GraphNode | None:
        compatible = _COMPATIBLE_TYPES[observation.unit.kind]
        observation_tokens = cls._tokens(
            " ".join(
                value
                for value in (
                    observation.unit.label,
                    observation.unit.interpreted_text,
                    canonical_json(observation.unit.structured_data)
                    if observation.unit.structured_data is not None
                    else None,
                )
                if value
            )
        )
        if not observation_tokens:
            return None

        scored: list[tuple[float, GraphNode]] = []
        for node in candidates:
            if node.id in matched_nodes or node.type not in compatible:
                continue
            node_tokens = cls._tokens(
                " ".join(value for value in (node.label, node.text) if value)
            )
            if not node_tokens:
                continue
            overlap = len(observation_tokens & node_tokens)
            if overlap == 0:
                continue
            text_score = overlap / min(len(observation_tokens), len(node_tokens))
            bbox_score = cls._bbox_iou(
                observation.locator.bbox,
                cls._matching_bbox(node),
            )
            # Text remains authoritative, while page-local geometry disambiguates
            # repeated labels and same-kind units. Missing geometry is neutral.
            score = text_score if bbox_score is None else 0.7 * text_score + 0.3 * bbox_score
            if text_score >= 0.5 and score >= 0.5:
                scored.append((score, node))
        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1].id))
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    @staticmethod
    def _tokens(value: str) -> frozenset[str]:
        return frozenset(
            token.casefold() for token in _TOKEN_RE.findall(value) if len(token) > 1
        )

    @staticmethod
    def _matching_bbox(
        node: GraphNode,
    ) -> tuple[float, float, float, float] | None:
        normalized = node.metadata.get("normalized_bbox")
        if isinstance(normalized, list) and len(normalized) == 4:
            values: list[float] = []
            for value in normalized:
                if not isinstance(value, (int, float)):
                    return node.locator.bbox
                values.append(float(value))
            return values[0], values[1], values[2], values[3]
        return node.locator.bbox

    @staticmethod
    def _bbox_iou(
        first: tuple[float, float, float, float] | None,
        second: tuple[float, float, float, float] | None,
    ) -> float | None:
        if first is None or second is None:
            return None
        intersection_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        intersection_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
        intersection = intersection_width * intersection_height
        first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
        second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
        union = first_area + second_area - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _evidence(
        native: ExtractedDocument,
        node: GraphNode,
        observation: VisualObservation,
    ) -> tuple[EvidenceRecord, EvidenceRecord]:
        key = (
            f"visual-observation:{node.id}:page={observation.locator.page}:"
            f"order={observation.unit.reading_order}"
        )
        source = EvidenceRecord(
            id=stable_id(native.document.content_hash, key + ":crop"),
            document_id=native.document.id,
            node_id=node.id,
            kind=(
                EvidenceKind.SOURCE_MEDIA
                if node.type is NodeType.IMAGE
                else EvidenceKind.RENDERED_PREVIEW
            ),
            locator=observation.locator,
            asset_hash=observation.crop_asset_hash,
            mime_type="image/png",
            data={
                "crop_height": observation.crop_height,
                "crop_width": observation.crop_width,
                "page_asset_hash": observation.page_asset_hash,
                "semantic_unit_kind": observation.unit.kind.value,
                "visual_observation": True,
            },
        )
        payload = observation.unit.structured_data
        if payload is None:
            payload = observation.unit.interpreted_text or observation.unit.label
        generated = EvidenceRecord(
            id=stable_id(native.document.content_hash, key + ":generated"),
            document_id=native.document.id,
            node_id=node.id,
            kind=EvidenceKind.GENERATED_EXTRACTION,
            locator=observation.locator,
            text=observation.unit.interpreted_text,
            data=payload,
            generated=True,
        )
        return source, generated

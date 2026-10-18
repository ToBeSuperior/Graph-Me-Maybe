from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from graphme.application.document_graph import DocumentGraphBuilder, UnitGrouper, VisualObserver
from graphme.application.embedding import EmbeddingGenerator
from graphme.application.grounding import GroundedDocumentBuilder
from graphme.application.hierarchy_decisions import (
    HierarchyScopeProvider,
)
from graphme.application.index_representation import MultimodalIndexComposer
from graphme.application.page_enrichment import PageEnricher
from graphme.application.relations import RelationReconciler
from graphme.application.semantic_reconciliation import SemanticReconciler
from graphme.application.xlsx_visual_binding import VisualBinder
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import EmbeddingRecord, ExtractedDocument, SubgraphRecord
from graphme.ports.extractor import DocumentExtractor


class DocumentIndexBuilder:
    """Build one complete in-memory index without exposing partial persistence."""

    PIPELINE_VERSION = "2"
    GROUNDED_EXTRACTOR_VERSION = "1"

    def __init__(
        self,
        *,
        grounding: GroundedDocumentBuilder,
        relations: RelationReconciler,
        composer: MultimodalIndexComposer,
        embeddings: EmbeddingGenerator,
        extractors: Mapping[DocumentFormat, DocumentExtractor] | None = None,
        native_xlsx: DocumentExtractor | None = None,
        semantic_reconciler: SemanticReconciler | None = None,
        visual_observer: VisualObserver | None = None,
        hierarchy_mode: Literal["off", "derived"] = "off",
        hierarchy_provider: HierarchyScopeProvider | None = None,
        visual_binder: VisualBinder | None = None,
        enricher: PageEnricher | None = None,
        unit_grouper: UnitGrouper | None = None,
    ) -> None:
        self._grounding = grounding
        self._relations = relations
        self._composer = composer
        self._embeddings = embeddings
        extractor_registry = dict(extractors or {})
        if native_xlsx is not None:
            extractor_registry.setdefault(DocumentFormat.XLSX, native_xlsx)
        for document_format, extractor in extractor_registry.items():
            if not isinstance(document_format, DocumentFormat):
                raise ValueError("extractor registry keys must be document formats")
            if extractor.format is not document_format:
                raise ValueError(
                    "extractor registry key must match extractor format: "
                    f"{document_format.value!r} != {extractor.format.value!r}"
                )
            if not getattr(extractor, "version", self.GROUNDED_EXTRACTOR_VERSION):
                raise ValueError("extractor version must not be empty")
        self._extractors = MappingProxyType(extractor_registry)
        # The rule-based outline agreed with a page-reading model only half the time,
        # so it is no longer written. "off" leaves the outline unstated; "derived" reads
        # it from the rendered page.
        if hierarchy_mode not in {"off", "derived"}:
            raise ValueError(f"unsupported hierarchy mode: {hierarchy_mode!r}")
        if hierarchy_mode == "derived" and hierarchy_provider is None:
            raise ValueError("the derived outline needs a hierarchy scope provider")
        if hierarchy_mode == "off" and hierarchy_provider is not None:
            raise ValueError("a hierarchy scope provider does nothing while off")
        self._hierarchy_mode = hierarchy_mode
        # One reading of a document, whoever asks for its graph: this index, the graph
        # space, the demo. The index only adds what retrieval needs on top.
        self.graph = DocumentGraphBuilder(
            extractors=self._extractors,
            grounding=grounding,
            semantic_reconciler=semantic_reconciler,
            visual_observer=visual_observer or grounding,
            visual_binder=visual_binder,
            hierarchy_provider=hierarchy_provider,
            unit_grouper=unit_grouper,
            enricher=enricher,
        )

    @property
    def pipeline_version(self) -> str:
        return self.PIPELINE_VERSION

    def extractor_version(self, document_format: DocumentFormat) -> str:
        extractor = self._extractors.get(document_format)
        return (
            str(getattr(extractor, "version", self.GROUNDED_EXTRACTOR_VERSION))
            if extractor is not None
            else self.GROUNDED_EXTRACTOR_VERSION
        )

    def build(
        self, *, source: Path, document_format: DocumentFormat
    ) -> tuple[
        ExtractedDocument,
        tuple[SubgraphRecord, ...],
        tuple[EmbeddingRecord, ...],
    ]:
        """The document's graph, and what retrieval needs on top of it."""
        extracted = self.graph.build(source, document_format)
        extracted = self._relations.reconcile(extracted)
        composed = self._composer.compose(extracted)
        subgraphs = tuple(item.subgraph for item in composed)
        embeddings = self._embeddings.embed(
            requests=tuple(
                (item.request, item.evidence) for item in composed
            ),
            persist=False,
        )
        return extracted, subgraphs, embeddings

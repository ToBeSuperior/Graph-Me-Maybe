from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from graphme.domain.enums import DocumentFormat
from graphme.domain.models import (
    EmbeddingRecord,
    ExtractedDocument,
    IndexManifest,
    IngestionRun,
    SubgraphRecord,
    WarningRecord,
)


class GraphStore(Protocol):
    def get_extracted_document(self, document_id: str) -> ExtractedDocument | None: ...

    def is_index_ready(self, document_id: str) -> bool: ...

    def get_subgraphs(self, document_id: str) -> tuple[SubgraphRecord, ...]: ...

    def get_embeddings(
        self, owner_ids: tuple[str, ...]
    ) -> tuple[EmbeddingRecord, ...]: ...

    def get_index_manifest(self, document_id: str) -> IndexManifest | None: ...

    def get_latest_ingestion_run(self, document_id: str) -> IngestionRun | None: ...


class InspectionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    source_path: Path
    file_name: str
    format: DocumentFormat
    content_hash: str
    ready: bool
    latest_ingestion_run: IngestionRun | None
    warnings: tuple[WarningRecord, ...]
    node_count: int
    edge_count: int
    evidence_count: int
    source_evidence_count: int
    generated_evidence_count: int
    subgraph_count: int
    embedding_count: int
    missing_embedding_count: int
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dimensions: int | None
    embedding_coverage: float

    def render_text(self) -> str:
        latest_run = self.latest_ingestion_run
        lines = (
            f"document_id={self.document_id}",
            f"source={self.source_path}",
            f"format={self.format.value}",
            f"content_hash={self.content_hash}",
            f"ready={str(self.ready).lower()}",
            f"latest_run_id={latest_run.id if latest_run else ''}",
            f"latest_run_status={latest_run.status.value if latest_run else ''}",
            f"nodes={self.node_count}",
            f"edges={self.edge_count}",
            f"evidence={self.evidence_count}",
            f"source_evidence={self.source_evidence_count}",
            f"generated_evidence={self.generated_evidence_count}",
            f"warnings={len(self.warnings)}",
            f"subgraphs={self.subgraph_count}",
            f"embeddings={self.embedding_count}",
            f"missing_embeddings={self.missing_embedding_count}",
            f"embedding_provider={self.embedding_provider or ''}",
            f"embedding_model={self.embedding_model or ''}",
            f"embedding_dimensions={self.embedding_dimensions or ''}",
            f"embedding_coverage={self.embedding_coverage:.3f}",
        )
        return "\n".join(lines)


class InspectionService:
    def __init__(self, *, store: GraphStore) -> None:
        self._store = store

    def graph(self, document_id: str) -> ExtractedDocument:
        extracted = self._store.get_extracted_document(document_id)
        if extracted is None:
            raise ValueError(f"document not found: {document_id}")
        return extracted

    def inspect_record(self, document_id: str) -> InspectionReport:
        extracted = self.graph(document_id)
        ready = self._store.is_index_ready(document_id)
        subgraphs = self._store.get_subgraphs(document_id)
        embeddings = self._store.get_embeddings(
            tuple(subgraph.id for subgraph in subgraphs)
        )
        manifest = self._store.get_index_manifest(document_id)
        embedded_owner_ids = {embedding.owner_id for embedding in embeddings}
        missing_embeddings = sum(
            subgraph.id not in embedded_owner_ids for subgraph in subgraphs
        )
        source_count = sum(not record.generated for record in extracted.evidence)
        document = extracted.document
        return InspectionReport(
            document_id=document_id,
            source_path=document.source_path,
            file_name=document.file_name,
            format=document.format,
            content_hash=document.content_hash,
            ready=ready,
            latest_ingestion_run=self._store.get_latest_ingestion_run(document_id),
            warnings=extracted.warnings,
            node_count=len(extracted.nodes),
            edge_count=len(extracted.edges),
            evidence_count=len(extracted.evidence),
            source_evidence_count=source_count,
            generated_evidence_count=len(extracted.evidence) - source_count,
            subgraph_count=len(subgraphs),
            embedding_count=len(embeddings),
            missing_embedding_count=missing_embeddings,
            embedding_provider=manifest.embedding_provider if manifest else None,
            embedding_model=manifest.embedding_model if manifest else None,
            embedding_dimensions=manifest.embedding_dimensions if manifest else None,
            embedding_coverage=(
                len(embeddings) / len(subgraphs) if subgraphs else 0.0
            ),
        )

    def inspect(self, document_id: str) -> str:
        return self.inspect_record(document_id).render_text()

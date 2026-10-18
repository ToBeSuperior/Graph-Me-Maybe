from pathlib import Path
from typing import Protocol

from graphme.domain.models import (
    Answer,
    DocumentRecord,
    EmbeddingRecord,
    EvidenceRecord,
    ExtractedDocument,
    IndexManifest,
    IngestionRun,
    SubgraphRecord,
)


class GraphStore(Protocol):
    def initialize(self) -> None: ...

    def save_extracted_document(self, extracted: ExtractedDocument) -> None: ...

    def get_document(self, document_id: str) -> DocumentRecord | None: ...

    def list_documents(self) -> tuple[DocumentRecord, ...]: ...

    def get_extracted_document(self, document_id: str) -> ExtractedDocument | None: ...

    def find_indexed_document_by_hash(
        self,
        content_hash: str,
        *,
        extractor_version: str | None = None,
        pipeline_version: str | None = None,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> DocumentRecord | None: ...

    def is_index_ready(self, document_id: str) -> bool: ...

    def get_index_manifest(self, document_id: str) -> IndexManifest | None: ...

    def save_index(
        self,
        extracted: ExtractedDocument,
        subgraphs: tuple[SubgraphRecord, ...],
        embeddings: tuple[EmbeddingRecord, ...],
        run: IngestionRun,
        *,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
        extractor_version: str | None = None,
        pipeline_version: str | None = None,
    ) -> None: ...

    def save_subgraphs(self, subgraphs: tuple[SubgraphRecord, ...]) -> None: ...

    def get_subgraphs(self, document_id: str) -> tuple[SubgraphRecord, ...]: ...

    def save_embeddings(self, embeddings: tuple[EmbeddingRecord, ...]) -> None: ...

    def get_embeddings(
        self, owner_ids: tuple[str, ...]
    ) -> tuple[EmbeddingRecord, ...]: ...

    def get_embedding_by_cache_key(self, cache_key: str) -> EmbeddingRecord | None: ...

    def get_evidence(self, evidence_ids: tuple[str, ...]) -> tuple[EvidenceRecord, ...]: ...

    def save_answer(self, answer: Answer) -> None: ...

    def save_ingestion_run(self, run: IngestionRun) -> None: ...

    def get_latest_ingestion_run(self, document_id: str) -> IngestionRun | None: ...

    def get_ingestion_runs(self, document_id: str | None = None) -> tuple[IngestionRun, ...]: ...


class AssetStore(Protocol):
    def put(self, content: bytes, suffix: str = "") -> str: ...

    def path_for(self, content_hash: str, suffix: str = "") -> Path: ...

    def read(self, content_hash: str, suffix: str = "") -> bytes: ...

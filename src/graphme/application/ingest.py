from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from graphme.domain.enums import DocumentFormat, IngestionStatus
from graphme.domain.ids import sha256_file, stable_id
from graphme.domain.models import (
    EmbeddingRecord,
    ExtractedDocument,
    IngestionRun,
    SubgraphRecord,
)
from graphme.ports.storage import GraphStore

SUFFIX_FORMATS = {
    ".xlsx": DocumentFormat.XLSX,
    ".pptx": DocumentFormat.PPTX,
    ".docx": DocumentFormat.DOCX,
    ".pdf": DocumentFormat.PDF,
}
"""The files read here, by extension."""


def format_of(source: Path) -> DocumentFormat:
    """The format a file is read as, by its extension."""
    fmt = SUFFIX_FORMATS.get(source.suffix.lower())
    if fmt is None:
        raise ValueError(f"unsupported document format: {source.suffix!r}")
    return fmt


def documents_in(folder: Path) -> list[Path]:
    """Every document under a folder that can be read, in a stable order.

    A folder is how a corpus arrives: everything in it, subfolders too. What is not one of
    the formats read here is passed over, and so is what is not the user's -- a lock file
    Office keeps beside an open document ("~$..."), anything hidden.
    """
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUFFIX_FORMATS
        and not path.name.startswith("~$")
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    )


def documents_at(path: Path) -> list[Path]:
    """What a path holds to be read: a folder's documents (`documents_in`), or one file --
    refused here, before anything is read, if it is not a format read here."""
    if path.is_dir():
        return documents_in(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    format_of(path)
    return [path]


class IndexBuilder(Protocol):
    @property
    def pipeline_version(self) -> str: ...

    def extractor_version(self, document_format: DocumentFormat) -> str: ...

    def build(
        self, *, source: Path, document_format: DocumentFormat
    ) -> tuple[
        ExtractedDocument,
        tuple[SubgraphRecord, ...],
        tuple[EmbeddingRecord, ...],
    ]: ...


class IngestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    content_hash: str
    reused: bool
    run: IngestionRun


class DocumentIngestor:
    """Build and atomically expose a complete index."""

    def __init__(
        self,
        *,
        store: GraphStore,
        index_builder: IndexBuilder,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        self._store = store
        self._index_builder = index_builder
        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    @staticmethod
    def _resolve_format(source: Path) -> DocumentFormat:
        return format_of(source)

    def ingest(self, source: Path) -> IngestResult:
        fmt = self._resolve_format(source)
        content_hash = sha256_file(source)
        run_id = stable_id(content_hash, f"ingestion:attempt:{uuid4()}")
        started_at = datetime.now(UTC)

        extractor_version = self._index_builder.extractor_version(fmt)
        pipeline_version = self._index_builder.pipeline_version
        existing = self._store.find_indexed_document_by_hash(
            content_hash,
            extractor_version=extractor_version,
            pipeline_version=pipeline_version,
            embedding_provider=self._embedding_provider,
            embedding_model=self._embedding_model,
            embedding_dimensions=self._embedding_dimensions,
        )
        if existing is not None:
            run = self._succeeded_run(
                run_id=run_id,
                document_id=existing.id,
                source=source,
                started_at=started_at,
            )
            self._store.save_ingestion_run(run)
            return IngestResult(
                document_id=existing.id,
                content_hash=content_hash,
                reused=True,
                run=run,
            )

        try:
            extracted, subgraphs, embeddings = self._index_builder.build(
                source=source,
                document_format=fmt,
            )
            if extracted.document.content_hash != content_hash:
                raise ValueError("indexed document hash does not match source content")
            if extracted.document.format is not fmt:
                raise ValueError("indexed document format does not match source suffix")
            run = self._succeeded_run(
                run_id=run_id,
                document_id=extracted.document.id,
                source=source,
                started_at=started_at,
            )
            self._store.save_index(
                extracted,
                subgraphs,
                embeddings,
                run,
                embedding_provider=self._embedding_provider,
                embedding_model=self._embedding_model,
                embedding_dimensions=self._embedding_dimensions,
                extractor_version=extractor_version,
                pipeline_version=pipeline_version,
            )
        except Exception as exc:
            self._save_failed_run(
                run_id=run_id,
                source=source,
                started_at=started_at,
                error=str(exc),
            )
            raise

        return IngestResult(
            document_id=extracted.document.id,
            content_hash=content_hash,
            reused=False,
            run=run,
        )

    def _save_failed_run(
        self,
        *,
        run_id: str,
        source: Path,
        started_at: datetime,
        error: str,
    ) -> None:
        self._store.save_ingestion_run(
            IngestionRun(
                id=run_id,
                source_path=source,
                status=IngestionStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                error=error,
            )
        )

    @staticmethod
    def _succeeded_run(
        *,
        run_id: str,
        document_id: str,
        source: Path,
        started_at: datetime,
    ) -> IngestionRun:
        return IngestionRun(
            id=run_id,
            document_id=document_id,
            source_path=source,
            status=IngestionStatus.SUCCEEDED,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )

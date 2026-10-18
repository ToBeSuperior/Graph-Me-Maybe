from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from graphme.domain.enums import IngestionStatus
from graphme.domain.models import (
    Answer,
    DocumentRecord,
    EmbeddingRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    IndexManifest,
    IngestionRun,
    SubgraphRecord,
    WarningRecord,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_document ON nodes(document_id);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_document ON edges(document_id);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    node_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_node ON evidence(node_id);

CREATE TABLE IF NOT EXISTS warnings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    source_hash TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_captions_source ON captions(source_hash);

CREATE TABLE IF NOT EXISTS subgraphs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_cache ON embeddings(cache_key);

CREATE TABLE IF NOT EXISTS answers (
    document_id TEXT NOT NULL REFERENCES documents(id),
    question TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indexed_documents (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    ready_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(id),
    embedding_provider TEXT,
    embedding_model TEXT,
    embedding_dimensions INTEGER,
    subgraph_count INTEGER,
    embedding_count INTEGER,
    extractor_version TEXT,
    pipeline_version TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_indexed_documents_hash
    ON indexed_documents(content_hash);
"""


class SQLiteGraphStore:
    """File-backed graph/evidence store over SQLite with JSON payload columns."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(indexed_documents)")
            }
            for name, sql_type in (
                ("embedding_provider", "TEXT"),
                ("embedding_model", "TEXT"),
                ("embedding_dimensions", "INTEGER"),
                ("subgraph_count", "INTEGER"),
                ("embedding_count", "INTEGER"),
                ("extractor_version", "TEXT"),
                ("pipeline_version", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE indexed_documents ADD COLUMN {name} {sql_type}"
                    )

    def save_extracted_document(self, extracted: ExtractedDocument) -> None:
        doc = extracted.document
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents(id, content_hash, payload) VALUES (?, ?, ?)",
                (doc.id, doc.content_hash, doc.model_dump_json()),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO nodes(id, document_id, payload) VALUES (?, ?, ?)",
                [(n.id, n.document_id, n.model_dump_json()) for n in extracted.nodes],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO edges(id, document_id, payload) VALUES (?, ?, ?)",
                [(e.id, e.document_id, e.model_dump_json()) for e in extracted.edges],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO evidence(id, document_id, node_id, payload)"
                " VALUES (?, ?, ?, ?)",
                [
                    (ev.id, ev.document_id, ev.node_id, ev.model_dump_json())
                    for ev in extracted.evidence
                ],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO warnings(id, document_id, payload) VALUES (?, ?, ?)",
                [(w.id, w.document_id, w.model_dump_json()) for w in extracted.warnings],
            )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        return DocumentRecord.model_validate_json(row[0]) if row else None

    def list_documents(self) -> tuple[DocumentRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM documents ORDER BY rowid DESC"
            ).fetchall()
        return tuple(DocumentRecord.model_validate_json(row[0]) for row in rows)

    def get_evidence_record(self, evidence_id: str) -> EvidenceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        return EvidenceRecord.model_validate_json(row[0]) if row else None

    def get_extracted_document(self, document_id: str) -> ExtractedDocument | None:
        document = self.get_document(document_id)
        if document is None:
            return None
        with self._connect() as conn:
            nodes = conn.execute(
                "SELECT payload FROM nodes WHERE document_id = ? ORDER BY rowid",
                (document_id,),
            ).fetchall()
            edges = conn.execute(
                "SELECT payload FROM edges WHERE document_id = ? ORDER BY rowid",
                (document_id,),
            ).fetchall()
            evidence = conn.execute(
                "SELECT payload FROM evidence WHERE document_id = ? ORDER BY rowid",
                (document_id,),
            ).fetchall()
            warnings = conn.execute(
                "SELECT payload FROM warnings WHERE document_id = ? ORDER BY rowid",
                (document_id,),
            ).fetchall()
        return ExtractedDocument(
            document=document,
            nodes=tuple(GraphNode.model_validate_json(row[0]) for row in nodes),
            edges=tuple(GraphEdge.model_validate_json(row[0]) for row in edges),
            evidence=tuple(
                EvidenceRecord.model_validate_json(row[0]) for row in evidence
            ),
            warnings=tuple(
                WarningRecord.model_validate_json(row[0]) for row in warnings
            ),
        )

    def find_indexed_document_by_hash(
        self,
        content_hash: str,
        *,
        extractor_version: str | None = None,
        pipeline_version: str | None = None,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> DocumentRecord | None:
        filters = ["indexed_documents.content_hash = ?"]
        parameters: list[str | int] = [content_hash]
        if extractor_version is not None:
            filters.append("indexed_documents.extractor_version = ?")
            parameters.append(extractor_version)
        if pipeline_version is not None:
            filters.append("indexed_documents.pipeline_version = ?")
            parameters.append(pipeline_version)
        for column, value in (
            ("embedding_provider", embedding_provider),
            ("embedding_model", embedding_model),
            ("embedding_dimensions", embedding_dimensions),
        ):
            if value is not None:
                filters.append(f"indexed_documents.{column} = ?")
                parameters.append(value)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT documents.payload "
                "FROM indexed_documents "
                "JOIN documents ON documents.id = indexed_documents.document_id "
                f"WHERE {' AND '.join(filters)} LIMIT 1",
                parameters,
            ).fetchone()
        return DocumentRecord.model_validate_json(row[0]) if row else None

    def is_index_ready(self, document_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM indexed_documents WHERE document_id = ? LIMIT 1",
                (document_id,),
            ).fetchone()
        return row is not None

    def get_index_manifest(self, document_id: str) -> IndexManifest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id, ready_at, embedding_provider, embedding_model, "
                "embedding_dimensions, subgraph_count, embedding_count, "
                "extractor_version, pipeline_version "
                "FROM indexed_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        (
            run_id,
            ready_at,
            provider,
            model,
            dimensions,
            subgraphs,
            embeddings,
            extractor_version,
            pipeline_version,
        ) = row
        if None in (provider, model, dimensions, subgraphs, embeddings):
            stored_subgraphs = self.get_subgraphs(document_id)
            stored_embeddings = self.get_embeddings(
                tuple(subgraph.id for subgraph in stored_subgraphs)
            )
            identities = {
                (record.provider, record.model, record.dimensions)
                for record in stored_embeddings
            }
            if len(identities) != 1:
                return None
            provider, model, dimensions = identities.pop()
            subgraphs, embeddings = len(stored_subgraphs), len(stored_embeddings)
        return IndexManifest(
            document_id=document_id,
            run_id=run_id,
            ready_at=datetime.fromisoformat(ready_at),
            embedding_provider=provider,
            embedding_model=model,
            embedding_dimensions=dimensions,
            subgraph_count=subgraphs,
            embedding_count=embeddings,
            extractor_version=extractor_version,
            pipeline_version=pipeline_version,
        )

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
    ) -> None:
        self._validate_index(
            extracted,
            subgraphs,
            embeddings,
            run,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        document_id = extracted.document.id
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM indexed_documents WHERE document_id = ?",
                (document_id,),
            )
            self._delete_document_index(conn, document_id)
            doc = extracted.document
            conn.execute(
                "INSERT INTO documents(id, content_hash, payload) VALUES (?, ?, ?)",
                (doc.id, doc.content_hash, doc.model_dump_json()),
            )
            conn.executemany(
                "INSERT INTO nodes(id, document_id, payload) VALUES (?, ?, ?)",
                [(node.id, node.document_id, node.model_dump_json()) for node in extracted.nodes],
            )
            conn.executemany(
                "INSERT INTO edges(id, document_id, payload) VALUES (?, ?, ?)",
                [(edge.id, edge.document_id, edge.model_dump_json()) for edge in extracted.edges],
            )
            conn.executemany(
                "INSERT INTO evidence(id, document_id, node_id, payload) VALUES (?, ?, ?, ?)",
                [
                    (record.id, record.document_id, record.node_id, record.model_dump_json())
                    for record in extracted.evidence
                ],
            )
            conn.executemany(
                "INSERT INTO warnings(id, document_id, payload) VALUES (?, ?, ?)",
                [
                    (warning.id, warning.document_id, warning.model_dump_json())
                    for warning in extracted.warnings
                ],
            )
            conn.executemany(
                "INSERT INTO subgraphs(id, document_id, payload) VALUES (?, ?, ?)",
                [
                    (subgraph.id, subgraph.document_id, subgraph.model_dump_json())
                    for subgraph in subgraphs
                ],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(id, cache_key, payload) VALUES (?, ?, ?)",
                [
                    (embedding.id, embedding.cache_key, embedding.model_dump_json())
                    for embedding in embeddings
                ],
            )
            conn.execute(
                "INSERT OR REPLACE INTO ingestion_runs(id, payload) VALUES (?, ?)",
                (run.id, run.model_dump_json()),
            )
            conn.execute(
                "INSERT INTO indexed_documents("
                "document_id, content_hash, ready_at, run_id, embedding_provider, "
                "embedding_model, embedding_dimensions, subgraph_count, embedding_count, "
                "extractor_version, pipeline_version"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    doc.content_hash,
                    datetime.now(UTC).isoformat(),
                    run.id,
                    embedding_provider or embeddings[0].provider,
                    embedding_model or embeddings[0].model,
                    embedding_dimensions or embeddings[0].dimensions,
                    len(subgraphs),
                    len(embeddings),
                    extractor_version,
                    pipeline_version,
                ),
            )

    @staticmethod
    def _validate_index(
        extracted: ExtractedDocument,
        subgraphs: tuple[SubgraphRecord, ...],
        embeddings: tuple[EmbeddingRecord, ...],
        run: IngestionRun,
        *,
        embedding_provider: str | None,
        embedding_model: str | None,
        embedding_dimensions: int | None,
    ) -> None:
        document_id = extracted.document.id
        if run.status is not IngestionStatus.SUCCEEDED:
            raise ValueError("complete index requires a succeeded ingestion run")
        if run.document_id != document_id:
            raise ValueError("ingestion run must belong to the indexed document")
        if not subgraphs:
            raise ValueError("complete index requires at least one subgraph")
        if len({subgraph.id for subgraph in subgraphs}) != len(subgraphs):
            raise ValueError("subgraph IDs must be unique")
        node_ids = {node.id for node in extracted.nodes}
        edge_ids = {edge.id for edge in extracted.edges}
        for subgraph in subgraphs:
            if subgraph.document_id != document_id:
                raise ValueError("every subgraph must belong to the indexed document")
            if not set(subgraph.node_ids).issubset(node_ids):
                raise ValueError("subgraph references an unknown node")
            if not set(subgraph.edge_ids).issubset(edge_ids):
                raise ValueError("subgraph references an unknown edge")
        embeddings_by_owner: dict[str, list[EmbeddingRecord]] = {}
        for embedding in embeddings:
            embeddings_by_owner.setdefault(embedding.owner_id, []).append(embedding)
        subgraph_ids = {subgraph.id for subgraph in subgraphs}
        if set(embeddings_by_owner) != subgraph_ids or any(
            len(records) != 1 for records in embeddings_by_owner.values()
        ):
            raise ValueError("every subgraph requires exactly one embedding")
        for subgraph in subgraphs:
            embedding = embeddings_by_owner[subgraph.id][0]
            if embedding.representation_hash != subgraph.representation_hash:
                raise ValueError("embedding representation hash must match subgraph")
            if (
                embedding_provider is not None
                and embedding.provider != embedding_provider
            ):
                raise ValueError("embedding provider must match configured provider")
            if embedding_model is not None and embedding.model != embedding_model:
                raise ValueError("embedding model must match configured model")
            if (
                embedding_dimensions is not None
                and embedding.dimensions != embedding_dimensions
            ):
                raise ValueError("embedding dimensions must match configured dimensions")

    @staticmethod
    def _delete_document_index(
        conn: sqlite3.Connection, document_id: str
    ) -> None:
        old_subgraph_rows = conn.execute(
            "SELECT id FROM subgraphs WHERE document_id = ?", (document_id,)
        ).fetchall()
        old_subgraph_ids = {row[0] for row in old_subgraph_rows}
        if old_subgraph_ids:
            embedding_rows = conn.execute(
                "SELECT id, payload FROM embeddings"
            ).fetchall()
            for embedding_id, payload in embedding_rows:
                record = EmbeddingRecord.model_validate_json(payload)
                if record.owner_id in old_subgraph_ids:
                    conn.execute("DELETE FROM embeddings WHERE id = ?", (embedding_id,))
        for table in (
            "answers",
            "captions",
            "subgraphs",
            "warnings",
            "evidence",
            "edges",
            "nodes",
        ):
            conn.execute(f"DELETE FROM {table} WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def save_subgraphs(self, subgraphs: tuple[SubgraphRecord, ...]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO subgraphs(id, document_id, payload) VALUES (?, ?, ?)",
                [(s.id, s.document_id, s.model_dump_json()) for s in subgraphs],
            )

    def get_subgraphs(self, document_id: str) -> tuple[SubgraphRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM subgraphs WHERE document_id = ? ORDER BY rowid",
                (document_id,),
            ).fetchall()
        return tuple(SubgraphRecord.model_validate_json(row[0]) for row in rows)

    def save_embeddings(self, embeddings: tuple[EmbeddingRecord, ...]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(id, cache_key, payload) VALUES (?, ?, ?)",
                [(e.id, e.cache_key, e.model_dump_json()) for e in embeddings],
            )

    def get_embeddings(
        self, owner_ids: tuple[str, ...]
    ) -> tuple[EmbeddingRecord, ...]:
        if not owner_ids:
            return ()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM embeddings ORDER BY rowid"
            ).fetchall()
        owner_id_set = set(owner_ids)
        return tuple(
            record
            for row in rows
            if (record := EmbeddingRecord.model_validate_json(row[0])).owner_id
            in owner_id_set
        )

    def get_embedding_by_cache_key(self, cache_key: str) -> EmbeddingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM embeddings WHERE cache_key = ? LIMIT 1",
                (cache_key,),
            ).fetchone()
        return EmbeddingRecord.model_validate_json(row[0]) if row else None

    def get_evidence(self, evidence_ids: tuple[str, ...]) -> tuple[EvidenceRecord, ...]:
        if not evidence_ids:
            return ()
        placeholders = ",".join("?" for _ in evidence_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM evidence WHERE id IN ({placeholders})",
                evidence_ids,
            ).fetchall()
        return tuple(EvidenceRecord.model_validate_json(row[0]) for row in rows)

    def save_answer(self, answer: Answer) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO answers(document_id, question, payload) VALUES (?, ?, ?)",
                (answer.document_id, answer.question, answer.model_dump_json()),
            )

    def save_ingestion_run(self, run: IngestionRun) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ingestion_runs(id, payload) VALUES (?, ?)",
                (run.id, run.model_dump_json()),
            )

    def get_latest_ingestion_run(self, document_id: str) -> IngestionRun | None:
        runs = self.get_ingestion_runs(document_id)
        return runs[0] if runs else None

    def get_ingestion_runs(
        self, document_id: str | None = None
    ) -> tuple[IngestionRun, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM ingestion_runs ORDER BY rowid DESC"
            ).fetchall()
        runs = tuple(IngestionRun.model_validate_json(row[0]) for row in rows)
        if document_id is None:
            return runs
        return tuple(run for run in runs if run.document_id == document_id)

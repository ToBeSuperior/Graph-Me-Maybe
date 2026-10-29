"""The Python API: one document read on its own, and the graph space a corpus is put in.

Everything here is assembled from `runtime`, the same way the CLI and the demo build it.
`graphme/__init__.py` re-exports the public names; anything else is internal.
"""
from __future__ import annotations

import tempfile
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from types import TracebackType

import httpx

from graphme.adapters.export.neo4j_import import write_import_bundle
from graphme.adapters.storage.graph_rag import GraphAnswer, GraphRag, GraphSource
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.application.corpus_build import BuildReport, StepListener, forget_documents
from graphme.application.ingest import documents_at, format_of
from graphme.config import Settings, load_settings
from graphme.domain.models import ExtractedDocument
from graphme.runtime import (
    build_corpus_graph,
    corpus_graph_builder,
    create_graph_rag,
    create_graph_space,
    create_neo4j_admin,
)

MODEL_WAIT = 600.0
"""Seconds one model call may take: a page read by the model is slow under load."""


def read(path: str | Path, *, settings: Settings | None = None) -> ExtractedDocument:
    """One document read as a corpus build reads it -- rendered, its outline and visuals read
    by the model -- without a graph space. Attachments inside it are not unpacked: each
    becomes a document of its own only when a corpus is built (`GraphMe.add`)."""
    if Path(path).is_dir():
        raise ValueError(f"read takes one file, not a folder: {path}")
    [source] = documents_at(Path(path))
    settings = settings or load_settings()
    with httpx.Client(timeout=MODEL_WAIT) as client:
        return corpus_graph_builder(settings, client).build(source, format_of(source))


class GraphMe:
    """The graph space: documents put in, linked, embedded, searched and asked, exported.

    One Neo4j connection and one HTTP client, opened when first needed and closed by
    `close()` or the end of a `with` block; used again after that, they open again. Not
    safe to share between threads.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._space: Neo4jGraphSpace | None = None
        self._rag: GraphRag | None = None
        self._client: httpx.Client | None = None

    def __enter__(self) -> GraphMe:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._space is not None:
                self._space.close()
        finally:
            try:
                if self._client is not None:
                    self._client.close()
            finally:
                self._space, self._rag, self._client = None, None, None

    def add(
        self, path: str | Path, *, again: bool = False, on_step: StepListener | None = None
    ) -> BuildReport:
        """A folder's documents, or one file, read and put in the space, then linked. What
        is in the space already is passed over unless `again`."""
        path = Path(path)
        documents_at(path)  # refused before a connection or a model call
        return build_corpus_graph(
            path, client=self._http(), settings=self.settings, on_step=on_step, again=again,
            space=self.graph_space(),
        )

    def embed(self, *, on_progress: Callable[[int, int], None] | None = None) -> int:
        """Passages cut and embedded for every document not embedded yet; how many."""
        return self.rag().embed(on_progress=on_progress)

    def search(self, question: str, *, documents: int | None = None,
               top_k: int | None = None) -> list[GraphSource]:
        """The passages a question finds, until `documents` documents are reached, each with
        the documents linked to its own. `top_k` is the old name of `documents`."""
        return self.rag().find(question, documents=self._documents(documents, top_k))

    def ask(self, question: str, *, documents: int | None = None,
            top_k: int | None = None) -> GraphAnswer:
        """An answer from what the question finds, citing its files. `top_k` is the old name
        of `documents`."""
        return self.rag().ask(question, documents=self._documents(documents, top_k))

    def _documents(self, documents: int | None, top_k: int | None) -> int:
        if top_k is not None:
            warnings.warn("top_k is now documents: how many documents a question finds",
                          DeprecationWarning, stacklevel=3)
        return documents or top_k or self.settings.retrieval_documents

    def documents(self) -> dict[str, str]:
        """Every document in the space: id -> file name."""
        space = self.graph_space()
        return space.files(space.document_ids())

    def delete(self, document_ids: Sequence[str]) -> None:
        """Documents taken out with their passages and links. Every id must be there."""
        if isinstance(document_ids, str):
            raise TypeError(f"delete takes a list of ids, not one string: {document_ids!r}")
        if not document_ids:
            return
        held = self.graph_space().files(document_ids)
        missing = [i for i in document_ids if i not in held]
        if missing:
            raise KeyError(f"not in the graph space: {', '.join(missing)}")
        forget_documents(self.graph_space(), list(document_ids))

    def reset(self) -> int:
        """Every document deleted, the indexes kept; how many were deleted."""
        return self.graph_space().reset()

    def export(
        self, target: str | Path, *, dump: bool = False, documents: Sequence[str] | None = None
    ) -> int:
        """The space as Neo4j loads it: a `neo4j-admin database import` folder, or with
        `dump` one `.dump` file (the app's own `neo4j-admin`, fetched on first use).
        `documents` limits it to those, linked only among themselves. How many documents were
        written."""
        target = Path(target)
        rows = self.graph_space().corpus_rows(list(documents) if documents is not None else None)
        if not dump:
            write_import_bundle(rows, target)
            return rows.documents
        with tempfile.TemporaryDirectory() as folder:
            write_import_bundle(rows, Path(folder))
            create_neo4j_admin(self.settings).dump(Path(folder), target)
        return rows.documents

    def graph_space(self) -> Neo4jGraphSpace:
        if self._space is None:
            self._space = create_graph_space(self.settings)
        return self._space

    @property
    def opened_space(self) -> Neo4jGraphSpace | None:
        """The graph space if it is open already; None, and nothing is opened."""
        return self._space

    def rag(self) -> GraphRag:
        """What embeds the space and answers questions over it, made on first use."""
        if self._rag is None:
            self._rag = create_graph_rag(self.settings, self.graph_space(), client=self._http())
        return self._rag

    def forget_rag(self) -> None:
        """The next question makes its retriever again: the keys or the model changed."""
        self._rag = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=MODEL_WAIT)
        return self._client

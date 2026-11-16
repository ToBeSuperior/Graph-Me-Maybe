"""A LangChain document loader: every document under the paths read as `graphme.read` reads
it, and cut as the graph space cuts it (`graphme.passages`) -- one LangChain `Document` per
passage, its metadata the passage's own (`file`, `format`, `headings`, `sheet`/`cells`,
`slide`, `pages`) and the units it holds (`node_ids`).

    from graphme.langchain import GraphMeLoader
    docs = GraphMeLoader("reports/", provider="openai", api_key="...").load()
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

try:
    from langchain_core.document_loaders import BaseLoader
    from langchain_core.documents import Document
except ImportError as error:  # the extra is not installed
    raise ImportError("graphme.langchain needs LangChain: "
                      "pip install 'graph-me-maybe[langchain]'") from error

from graphme.api import read
from graphme.application.ingest import documents_at
from graphme.config import Settings, load_settings
from graphme.passages import passages


class GraphMeLoader(BaseLoader):
    """Documents under `paths` (files or folders, folders read whole) as LangChain documents.
    Settings as `graphme.read` takes them, read once for every file."""

    def __init__(self, paths: str | Path | Sequence[str | Path], *,
                 settings: Settings | None = None, **in_code: Any) -> None:
        self._paths = [Path(paths)] if isinstance(paths, str | Path) else [Path(p) for p in paths]
        self._settings = settings
        self._in_code = in_code

    def lazy_load(self) -> Iterator[Document]:
        settings = self._settings or load_settings(**self._in_code)
        for path in self._paths:
            for source in documents_at(path):
                for passage in passages(read(source, settings=settings)):
                    yield Document(page_content=passage.text, metadata={
                        **passage.metadata, "node_ids": list(passage.node_ids)})

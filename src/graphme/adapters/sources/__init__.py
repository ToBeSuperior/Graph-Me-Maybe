"""The places originals are read from (`ports.sources.SourceStore`)."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from graphme.ports.sources import SourceStore


class LocalSource:
    """A file on this machine is its own copy."""

    @contextmanager
    def open_copy(self, origin: str) -> Iterator[Path]:
        path = Path(origin)
        if not path.is_file():
            raise FileNotFoundError(origin)
        yield path

    def web_url(self, origin: str) -> str | None:
        return None


class Sources:
    """Each origin to the store its scheme names (`gdrive://...`); a plain path is local."""

    def __init__(self, by_scheme: Mapping[str, SourceStore] | None = None) -> None:
        self._by_scheme = dict(by_scheme or {})
        self._local = LocalSource()

    def add(self, scheme: str, store: SourceStore) -> None:
        self._by_scheme[scheme] = store

    def _store(self, origin: str) -> SourceStore:
        scheme, found, _ = origin.partition("://")
        return self._by_scheme.get(scheme, self._local) if found else self._local

    def open_copy(self, origin: str) -> AbstractContextManager[Path]:
        return self._store(origin).open_copy(origin)

    def web_url(self, origin: str) -> str | None:
        return self._store(origin).web_url(origin)

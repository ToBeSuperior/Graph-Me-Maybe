"""Where a document's original is: this machine, or a cloud drive."""
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol


class SourceStore(Protocol):
    """The originals of documents, by the place the graph space records (`source_path`)."""

    def open_copy(self, origin: str) -> AbstractContextManager[Path]:
        """A local file holding the original while the context lasts. FileNotFoundError when
        it is gone, or no longer readable to this user."""
        ...

    def web_url(self, origin: str) -> str | None:
        """Where a reader opens the original in a browser, if it has such a place."""
        ...

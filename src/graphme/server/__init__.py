"""The HTTP server (`graphme serve`): the pipeline and the graph space over `/v1`."""
from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    # The web framework loads with the app alone: the workspace registry here is the library's
    # too (`GraphMe(workspace=...)`), and the core installs without the server.
    if name == "create_app":
        from graphme.server.app import create_app

        return create_app
    raise AttributeError(name)

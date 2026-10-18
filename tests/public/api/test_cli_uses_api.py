"""The CLI's graph-space commands go through the public API."""
from __future__ import annotations

import inspect

from graphme import cli


def test_the_cli_builds_nothing_of_its_own() -> None:
    source = inspect.getsource(cli)
    for assembled in ("create_graph_space(", "create_graph_rag(", "build_corpus_graph("):
        assert assembled not in source

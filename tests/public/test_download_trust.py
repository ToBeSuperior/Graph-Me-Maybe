"""What the app downloads itself -- Neo4j, Java, the search models -- is verified against the
system's own trust store. Python's bundled view of it missed a root Windows had not fetched
yet: Java's download from github.com failed its certificate check on a fresh PC, and the graph
DB never started."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
import truststore

from graphme.adapters import local_models
from graphme.adapters.storage import local_neo4j


class _Answer(io.BytesIO):
    headers: dict[str, str] = {}

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.mark.parametrize("fetch", [
    lambda target: local_neo4j._fetch("https://example.invalid/a", target),
    lambda target: local_neo4j._read("https://example.invalid/a"),
    lambda target: local_models._fetch("https://example.invalid/a", target),
])
def test_every_download_checks_its_certificate_with_the_systems_trust(
    fetch: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts: list[object] = []

    def urlopen(request: object, timeout: float, context: object = None) -> _Answer:
        contexts.append(context)
        return _Answer(b"x")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    fetch(tmp_path / "file")

    assert len(contexts) == 1 and isinstance(contexts[0], truststore.SSLContext)

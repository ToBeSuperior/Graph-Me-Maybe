"""A search model run on this machine is fetched by the library the first time it is needed,
as the app fetches it at start."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphme import api
from graphme.config import load_settings


class _Models:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def installed(self, model: Any) -> bool:
        return model.file in self.fetched

    def install(self, model: Any, on_progress: Any = None) -> None:
        self.fetched.append(model.file)


def test_searching_with_a_local_model_fetches_it_once(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    models = _Models()
    monkeypatch.setattr(api, "local_models", lambda root: models)
    settings = load_settings({"GRAPHME_PROFILE": "test", "GRAPHME_EMBEDDING_PROVIDER": "general",
                              "GRAPHME_HOME": str(tmp_path)})
    graph = api.GraphMe(settings)

    class _Rag:
        def find(self, question: str, *, documents: int) -> list[Any]:
            return []

    monkeypatch.setattr(graph, "rag", lambda: _Rag())
    graph.search("invented question")
    graph.search("invented question")

    assert len(models.fetched) == 1

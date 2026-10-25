"""Searching with a model run on this machine: two to choose from, the general one by default,
no key to ask for, questions written the way the model was trained to read them. Invented data
only; no model is fetched or run."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphme import model_endpoint, runtime
from graphme.adapters.embedding import OpenAIBatchEmbeddings
from graphme.config import load_settings
from graphme.providers import LOCAL_EMBEDDERS


def test_search_runs_on_this_machine_by_default_with_the_general_model() -> None:
    settings = load_settings(environ={})

    assert settings.embedding_provider == "general"
    assert settings.embedding_key is None
    assert model_endpoint.EMBEDDERS["general"].model_file is not None


def test_there_is_a_general_and_a_korean_model_to_choose_and_neither_needs_a_key() -> None:
    assert set(LOCAL_EMBEDDERS) == {"general", "korean"}
    for row in LOCAL_EMBEDDERS.values():
        assert row.key_env == "" and row.model_file is not None
        assert row.model_file.url.startswith("https://huggingface.co/")
        assert len(row.model_file.sha256) == 64
    assert set(LOCAL_EMBEDDERS) <= set(model_endpoint.EMBEDDERS)


def test_each_local_model_has_its_own_relevance_floor() -> None:
    for name in LOCAL_EMBEDDERS:
        settings = load_settings(environ={"GRAPHME_EMBEDDING_PROVIDER": name})
        assert settings.relevance_floor != 0.3  # calibrated, not the uncalibrated default


class _Embeddings:
    def __init__(self) -> None:
        self.sent: list[list[str]] = []

    def create(self, *, input: list[str], model: str, encoding_format: str) -> Any:
        self.sent.append(list(input))

        class Item:
            def __init__(self, index: int) -> None:
                self.index, self.embedding = index, [1.0, 0.0]

        class Answer:
            data = [Item(i) for i in range(len(input))]

        return Answer()


def test_a_question_carries_the_models_instruction_and_a_passage_does_not() -> None:
    embedder = OpenAIBatchEmbeddings(model="m", api_key="local", base_url="http://127.0.0.1:1/v1",
                                     query_prefix="Instruct: find it\nQuery: ")
    fake = _Embeddings()
    embedder.client.embeddings = fake  # type: ignore[assignment]

    embedder.embed_texts(["a passage"])
    embedder.embed_query("a question")

    assert fake.sent == [["a passage"], ["Instruct: find it\nQuery: a question"]]


def test_a_local_model_is_searched_at_the_address_its_server_was_started_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    served: list[str] = []

    class Models:
        def serve(self, model: Any) -> str:
            served.append(model.file)
            return "http://127.0.0.1:5999/v1"

    monkeypatch.setattr(runtime, "local_models", lambda root: Models())
    settings = load_settings(environ={"GRAPHME_HOME": str(tmp_path)})

    embedder = runtime.create_embedder(settings)

    assert served == [LOCAL_EMBEDDERS["general"].model_file.file]  # type: ignore[union-attr]
    assert str(embedder.client.base_url).rstrip("/") == "http://127.0.0.1:5999/v1"
    assert embedder.query_prefix == LOCAL_EMBEDDERS["general"].query_prefix

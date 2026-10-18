"""Each key is asked for by the variable that holds it, before anything is called."""
from __future__ import annotations

import pytest

from graphme.config import Settings


def test_embedding_names_the_key_it_needs() -> None:
    from graphme.runtime import create_embedder

    with pytest.raises(
        ValueError,
        match=r"OPENAI_API_KEY is required to embed or search "
        r"\(GRAPHME_EMBEDDING_PROVIDER=openai\)",
    ):
        create_embedder(Settings(embedding_provider="openai"))


def test_the_test_embedder_cannot_search_a_graph_space() -> None:
    from graphme.runtime import create_embedder

    with pytest.raises(ValueError, match="no provider embedder"):
        create_embedder(Settings(profile="test", embedding_provider="hash"))


def test_the_embedder_goes_to_its_own_address() -> None:
    from graphme import model_endpoint
    from graphme.runtime import create_embedder

    embedder = create_embedder(Settings(embedding_provider="openai", embedding_key="sk-test"))

    assert str(embedder.client.base_url).rstrip("/") == (
        model_endpoint.EMBEDDERS["openai"].base_url)


def test_questions_over_the_graph_ask_for_the_chat_key_first(monkeypatch) -> None:
    """A provider that needs a key is refused by name before the graph is asked anything."""
    from types import SimpleNamespace

    from graphme import model_endpoint
    from graphme.runtime import create_graph_rag

    monkeypatch.setattr(model_endpoint, "KEY_REQUIRED", True)
    settings = Settings(embedding_provider="openai", embedding_key="sk-test")
    key_env = model_endpoint.PROVIDERS[settings.provider].key_env

    with pytest.raises(ValueError, match=rf"{key_env} is required \(GRAPHME_PROVIDER="):
        create_graph_rag(settings, SimpleNamespace(driver=None))  # type: ignore[arg-type]


@pytest.mark.parametrize(("embedder", "hinted"), [("openai", True), ("gemini", False)])
def test_the_gemini_hint_is_only_for_an_openai_embedder(monkeypatch, embedder, hinted) -> None:
    from graphme import model_endpoint
    from graphme.runtime import create_embedder

    monkeypatch.setitem(model_endpoint.EMBEDDERS, embedder, model_endpoint.EMBEDDERS["openai"])
    settings = Settings().model_copy(update={
        "provider": "anthropic", "embedding_provider": embedder, "embedding_key": None})

    with pytest.raises(ValueError) as raised:
        create_embedder(settings)

    assert ("set it to gemini" in str(raised.value)) is hinted

"""The one-document index embeds the words of each request with the provider's embedder."""
from __future__ import annotations

import json

import httpx
import pytest

from graphme.adapters.storage.graph_rag import OpenAIBatchEmbeddings
from graphme.domain.enums import EmbeddingInputType
from graphme.domain.ids import sha256_bytes
from graphme.domain.models import EmbeddingRequest, ImagePart, TextPart


def _embedder(seen: list[httpx.Request], width: int = 2) -> OpenAIBatchEmbeddings:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        texts = json.loads(request.content)["input"]
        data = [{"object": "embedding", "index": i, "embedding": [0.5] * width}
                for i in reversed(range(len(texts)))]
        return httpx.Response(200, json={"object": "list", "data": data, "model": "embed-test",
                                         "usage": {"prompt_tokens": 1, "total_tokens": 1}})

    return OpenAIBatchEmbeddings(model="embed-test", api_key="k-test",
                                 base_url="https://embed.test/v1", max_retries=0,
                                 http_client=httpx.Client(transport=httpx.MockTransport(handle)))


def _provider(seen: list[httpx.Request], width: int = 2):  # type: ignore[no-untyped-def]
    from graphme.adapters.embedding.text import TextEmbeddingProvider

    return TextEmbeddingProvider(name="openai", model="embed-test", dimensions=2,
                                 embedder=_embedder(seen, width))


IMAGE = ImagePart(asset_hash="a" * 64, mime_type="image/png", width=4, height=4)


def _request(request_id: str, *parts: object) -> EmbeddingRequest:
    return EmbeddingRequest(id=request_id, input_type=EmbeddingInputType.DOCUMENT,
                            parts=parts,  # type: ignore[arg-type]
                            representation_hash=sha256_bytes(request_id.encode()))


def test_only_the_words_are_sent_and_each_record_is_the_requests() -> None:
    seen: list[httpx.Request] = []
    requests = (_request("r1", TextPart(text="first"), IMAGE, TextPart(text="caption")),
                _request("r2", TextPart(text="second")))

    records = _provider(seen).embed(requests)

    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://embed.test/v1/embeddings"
    assert seen[0].headers["authorization"] == "Bearer k-test"
    assert body["input"] == ["first\ncaption", "second"]
    assert body["encoding_format"] == "float"
    assert "image" not in seen[0].content.decode()
    assert [(r.owner_id, r.provider, r.model, r.dimensions, r.representation_hash)
            for r in records] == [
        ("r1", "openai", "embed-test", 2, requests[0].representation_hash),
        ("r2", "openai", "embed-test", 2, requests[1].representation_hash)]


def test_a_request_with_no_words_is_refused_before_any_call() -> None:
    seen: list[httpx.Request] = []

    with pytest.raises(ValueError, match="r1"):
        _provider(seen).embed((_request("r1", IMAGE),))
    assert seen == []


def test_a_vector_of_another_size_is_refused() -> None:
    with pytest.raises(RuntimeError, match="2 finite numbers"):
        _provider([], width=3).embed((_request("r1", TextPart(text="x")),))

"""The one-document index's embedder: the provider text embedding the graph space uses.

An index request carries the subgraph's canonical text and the crops of its visuals; only the
text is embedded -- a picture is found by the words the subgraph holds about it.
"""
from __future__ import annotations

from math import isfinite

from graphme.adapters.storage.graph_rag import BatchEmbedder
from graphme.domain.ids import content_id, sha256_bytes
from graphme.domain.models import EmbeddingRecord, EmbeddingRequest, TextPart


class TextEmbeddingProvider:
    def __init__(self, *, name: str, model: str, dimensions: int,
                 embedder: BatchEmbedder) -> None:
        self._name = name
        self._model = model
        self._dimensions = dimensions
        self._embedder = embedder

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def cache_key(self, request: EmbeddingRequest) -> str:
        return (
            f"{self._name}:{self._model}:{request.input_type.value}:"
            f"{request.representation_hash}"
        )

    def embed(self, requests: tuple[EmbeddingRequest, ...]) -> tuple[EmbeddingRecord, ...]:
        if not requests:
            raise ValueError("at least one embedding request is required")
        texts = []
        for request in requests:
            text = "\n".join(p.text for p in request.parts if isinstance(p, TextPart))
            if not text.strip():
                raise ValueError(f"embedding request {request.id} has no text to embed")
            texts.append(text)
        vectors = self._embedder.embed_texts(texts)
        if len(vectors) != len(requests):
            raise RuntimeError("the embedder returned an unexpected result count")
        records = []
        for request, raw in zip(requests, vectors, strict=True):
            vector = tuple(float(value) for value in raw)
            if len(vector) != self._dimensions or not all(isfinite(v) for v in vector):
                raise RuntimeError(
                    f"the embedder returned a vector that is not {self._dimensions} finite numbers"
                )
            key = self.cache_key(request)
            records.append(EmbeddingRecord(
                id=content_id("embedding", sha256_bytes(key.encode())),
                owner_id=request.id,
                provider=self._name,
                model=self._model,
                dimensions=len(vector),
                vector=vector,
                representation_hash=request.representation_hash,
                cache_key=key,
            ))
        return tuple(records)

from typing import Protocol

from graphme.domain.models import EmbeddingRecord, EmbeddingRequest


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def cache_key(self, request: EmbeddingRequest) -> str: ...

    def embed(self, requests: tuple[EmbeddingRequest, ...]) -> tuple[EmbeddingRecord, ...]: ...

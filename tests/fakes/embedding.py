from graphme.domain.ids import content_id, sha256_bytes
from graphme.domain.models import EmbeddingRecord, EmbeddingRequest


class HashEmbeddingProvider:
    def __init__(self, *, dimensions: int) -> None:
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return "hash"

    @property
    def model(self) -> str:
        return "deterministic-sha256"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def cache_key(self, request: EmbeddingRequest) -> str:
        return (
            f"{self.name}:{self.model}:{request.input_type.value}:"
            f"{request.representation_hash}"
        )

    def embed(
        self, requests: tuple[EmbeddingRequest, ...]
    ) -> tuple[EmbeddingRecord, ...]:
        records = []
        for request in requests:
            cache_key = self.cache_key(request)
            digest = bytes.fromhex(sha256_bytes(cache_key.encode()))
            vector = tuple(
                digest[index % len(digest)] / 255.0
                for index in range(self._dimensions)
            )
            records.append(
                EmbeddingRecord(
                    id=content_id(
                        "embedding",
                        sha256_bytes(cache_key.encode()),
                    ),
                    owner_id=request.id,
                    provider=self.name,
                    model=self.model,
                    dimensions=self._dimensions,
                    vector=vector,
                    representation_hash=request.representation_hash,
                    cache_key=cache_key,
                )
            )
        return tuple(records)

from graphme import model_endpoint
from graphme.application.text_match import estimated_tokens
from graphme.domain.ids import sha256_bytes
from graphme.domain.models import (
    EmbeddingRecord,
    EmbeddingRequest,
    EvidenceRecord,
    TextPart,
)
from graphme.ports.embedding import EmbeddingProvider
from graphme.ports.generation import GenerationProvider
from graphme.ports.storage import GraphStore
from graphme.providers import Embedder


class EmbeddingGenerator:
    def __init__(
        self,
        *,
        generation_provider: GenerationProvider,
        embedding_provider: EmbeddingProvider,
        store: GraphStore | None = None,
        limits: Embedder | None = None,
    ) -> None:
        self._generation_provider = generation_provider
        self._embedding_provider = embedding_provider
        self._store = store
        self._limits = limits or model_endpoint.EMBEDDERS["openai"]
        """The embedder's input limits; the test embedder borrows OpenAI's."""

    def embed(
        self,
        *,
        requests: tuple[tuple[EmbeddingRequest, tuple[EvidenceRecord, ...]], ...],
        persist: bool = True,
    ) -> tuple[EmbeddingRecord, ...]:
        prepared: list[EmbeddingRequest] = []
        for request, evidence in requests:
            limit = self._limits.tokens_per_input
            if _estimate_tokens(request) > limit:
                summary = self._generation_provider.summarize_embedding_input(
                    evidence=evidence,
                    reason=f"embedding input exceeds {limit:,} tokens",
                )
                request = EmbeddingRequest(
                    id=request.id,
                    input_type=request.input_type,
                    parts=(TextPart(text=summary),),
                    representation_hash=sha256_bytes(summary.encode()),
                )
                if _estimate_tokens(request) > limit:
                    raise ValueError(f"generated embedding summary exceeds {limit:,} tokens")

            prepared.append(request)

        prepared_tuple = tuple(prepared)
        if self._store is None:
            return self._embed_batches(prepared_tuple)

        cached = tuple(
            self._store.get_embedding_by_cache_key(
                self._embedding_provider.cache_key(request)
            )
            for request in prepared_tuple
        )
        misses = tuple(
            request
            for request, record in zip(prepared, cached, strict=True)
            if record is None
        )
        if not misses:
            return tuple(record for record in cached if record is not None)
        generated = self._embed_batches(misses)
        if persist:
            self._store.save_embeddings(generated)
        generated_iter = iter(generated)
        return tuple(
            record if record is not None else next(generated_iter)
            for record in cached
        )

    def _embed_batches(
        self,
        requests: tuple[EmbeddingRequest, ...],
    ) -> tuple[EmbeddingRecord, ...]:
        records: list[EmbeddingRecord] = []
        batch: list[EmbeddingRequest] = []
        batch_tokens = 0
        for request in requests:
            estimated = _estimate_tokens(request)
            if batch and (
                batch_tokens + estimated > self._limits.tokens_per_request
                or len(batch) == self._limits.inputs_per_request
            ):
                records.extend(self._embed_batch(tuple(batch)))
                batch = []
                batch_tokens = 0
            batch.append(request)
            batch_tokens += estimated
        if batch:
            records.extend(self._embed_batch(tuple(batch)))
        return tuple(records)

    def _embed_batch(
        self,
        requests: tuple[EmbeddingRequest, ...],
    ) -> tuple[EmbeddingRecord, ...]:
        generated = self._embedding_provider.embed(requests)
        _validate_results(requests, generated)
        return generated


def _validate_results(
    requests: tuple[EmbeddingRequest, ...],
    records: tuple[EmbeddingRecord, ...],
) -> None:
    if len(records) != len(requests):
        raise RuntimeError(
            "embedding provider returned an unexpected result count"
        )
    if any(
        record.owner_id != request.id
        or record.representation_hash != request.representation_hash
        for request, record in zip(requests, records, strict=True)
    ):
        raise RuntimeError("embedding provider returned a mismatched result")


def _estimate_tokens(request: EmbeddingRequest) -> int:
    """The tokens the embedder is sent: the request's text. Images are not sent."""
    return sum(
        estimated_tokens(part.text) for part in request.parts if isinstance(part, TextPart)
    )

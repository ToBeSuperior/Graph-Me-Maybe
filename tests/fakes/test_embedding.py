from graphme.domain.enums import EmbeddingInputType
from graphme.domain.ids import sha256_bytes
from graphme.domain.models import EmbeddingRequest, TextPart
from tests.fakes.embedding import HashEmbeddingProvider


def test_hash_embedding_is_deterministic() -> None:
    text = "Revenue reached 100."
    request = EmbeddingRequest(
        id="caption-1",
        input_type=EmbeddingInputType.DOCUMENT,
        parts=(TextPart(text=text),),
        representation_hash=sha256_bytes(text.encode()),
    )
    provider = HashEmbeddingProvider(dimensions=4)

    first = provider.embed((request,))[0]
    second = provider.embed((request,))[0]

    assert first == second
    assert first.owner_id == request.id
    assert first.provider == "hash"
    assert first.model == "deterministic-sha256"
    assert first.dimensions == 4
    assert len(first.vector) == 4
    assert first.representation_hash == request.representation_hash
    assert first.cache_key == provider.cache_key(request)

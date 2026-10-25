"""The embedders a graph space's passages and questions are turned into vectors with."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.embeddings.base import Embedder


class BatchEmbedder(Embedder):
    """Embeds a question, and many passages in one call -- a whole space one call a passage
    takes far too long."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIBatchEmbeddings(OpenAIEmbeddings, BatchEmbedder):
    """OpenAI's embeddings endpoint, or a provider's compatible one (`base_url`). Vectors are
    asked for as floats -- the client otherwise asks for base64, which a compatible layer need
    not speak -- and put back in the order the texts were sent. A question is written after
    `query_prefix`, the instruction the model was trained to read one with; a passage goes as
    it is."""

    def __init__(self, *args: Any, query_prefix: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.query_prefix = query_prefix

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            input=texts, model=self.model, encoding_format="float")
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        return self.embed_texts([self.query_prefix + text])[0]

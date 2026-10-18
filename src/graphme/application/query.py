from math import isfinite
from typing import Any, cast

from graphme.application.answer_generation import AnswerGenerator
from graphme.application.retrieve import (
    assemble_retrieved_context,
    expand_ranked_nodes,
    rank_subgraphs,
)
from graphme.config import Settings
from graphme.domain.enums import EmbeddingInputType
from graphme.domain.ids import content_id, sha256_bytes
from graphme.domain.models import Answer, EmbeddingRequest, RetrievedContext, TextPart
from graphme.ports.embedding import EmbeddingProvider
from graphme.ports.generation import GenerationProvider
from graphme.ports.storage import GraphStore


class QueryResult:
    """Answer plus the retrieval context used to ground it."""

    def __init__(self, *, answer: Answer, context: RetrievedContext) -> None:
        self.answer = answer
        self.context = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self.answer, name)


class QueryService:
    def __init__(
        self,
        *,
        store: GraphStore,
        embedding_provider: EmbeddingProvider,
        generation_provider: GenerationProvider,
        settings: Settings,
    ) -> None:
        self._store = store
        self._embedding_provider = embedding_provider
        self._answer_generator = AnswerGenerator(store=store, provider=generation_provider)
        self._settings = settings

    def query(self, *, document_id: str, question: str) -> Answer:
        return self.query_detailed(document_id=document_id, question=question).answer

    def query_detailed(self, *, document_id: str, question: str) -> QueryResult:
        if not self._store.is_index_ready(document_id):
            raise ValueError(f"index not ready: {document_id}")
        extracted = self._store.get_extracted_document(document_id)
        if extracted is None:
            raise ValueError(f"document not found: {document_id}")

        manifest = self._store.get_index_manifest(document_id)
        if manifest is None:
            raise ValueError(f"index manifest unavailable: {document_id}")
        active_identity = (
            self._embedding_provider.name,
            self._embedding_provider.model,
            self._embedding_provider.dimensions,
        )
        indexed_identity = (
            manifest.embedding_provider,
            manifest.embedding_model,
            manifest.embedding_dimensions,
        )
        if active_identity != indexed_identity:
            raise ValueError(
                "query embedder is incompatible with document index: "
                f"active={active_identity!r}, indexed={indexed_identity!r}; "
                "run `graphme ingest` again"
            )
        subgraphs = self._store.get_subgraphs(document_id)
        embeddings = self._store.get_embeddings(tuple(subgraph.id for subgraph in subgraphs))
        if manifest.subgraph_count != len(subgraphs) or manifest.embedding_count != len(embeddings):
            raise ValueError("document index does not match its manifest")
        subgraphs_by_id = {subgraph.id: subgraph for subgraph in subgraphs}
        if len(embeddings) != len(subgraphs) or any(
            (subgraph := subgraphs_by_id.get(embedding.owner_id)) is None
            or embedding.representation_hash != subgraph.representation_hash
            or embedding.provider != manifest.embedding_provider
            or embedding.model != manifest.embedding_model
            or embedding.dimensions != manifest.embedding_dimensions
            or len(embedding.vector) != manifest.embedding_dimensions
            or any(not isfinite(value) for value in embedding.vector)
            for embedding in embeddings
        ):
            raise ValueError("stored embeddings do not match the document index")
        representation_hash = sha256_bytes(question.encode())
        request = EmbeddingRequest(
            id=content_id("query", representation_hash),
            input_type=EmbeddingInputType.QUERY,
            parts=(TextPart(text=question),),
            representation_hash=representation_hash,
        )
        query_embeddings = self._embedding_provider.embed((request,))
        if len(query_embeddings) != 1:
            raise RuntimeError("query embedder must return exactly one record")
        query_embedding = query_embeddings[0]
        if (
            query_embedding.owner_id != request.id
            or query_embedding.representation_hash != request.representation_hash
        ):
            raise RuntimeError("query embedder returned a mismatched record")
        if (
            query_embedding.provider != manifest.embedding_provider
            or query_embedding.model != manifest.embedding_model
            or query_embedding.dimensions != manifest.embedding_dimensions
            or len(query_embedding.vector) != manifest.embedding_dimensions
            or any(not isfinite(value) for value in query_embedding.vector)
        ):
            raise RuntimeError("query embedder returned an incompatible vector")
        hits, node_scores = rank_subgraphs(
            query_vector=query_embedding.vector, subgraphs=subgraphs, embeddings=embeddings
        )
        hits = hits[: self._settings.retrieval_top_k]
        selected_scores = {
            node_id: node_scores[node_id] for hit in hits for node_id in hit.node_ids
        }
        ranked_node_ids = tuple(
            sorted(selected_scores, key=selected_scores.__getitem__, reverse=True)
        )
        expanded = expand_ranked_nodes(
            ranked_node_ids=ranked_node_ids,
            nodes=extracted.nodes,
            edges=extracted.edges,
            max_nodes=self._settings.node_budget,
            max_tokens=self._settings.token_budget,
            source_evidence_node_ids=tuple(
                record.node_id for record in extracted.evidence if not record.generated
            ),
            max_hops=self._settings.max_hops,
            include_hops=True,
        )
        selected_node_ids, estimated_tokens, hops_used = cast(
            tuple[tuple[str, ...], int, int], expanded
        )

        context = assemble_retrieved_context(
            document_id=document_id,
            question=question,
            hits=hits,
            node_scores=selected_scores,
            selected_node_ids=selected_node_ids,
            evidence=extracted.evidence,
            hops_used=hops_used,
            estimated_tokens=estimated_tokens,
        )
        return QueryResult(answer=self._answer_generator.generate(context=context), context=context)

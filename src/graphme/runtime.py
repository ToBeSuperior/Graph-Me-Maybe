from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from graphme import model_endpoint
from graphme.adapters.chat import Endpoint
from graphme.adapters.embedding.text import TextEmbeddingProvider
from graphme.adapters.export.neo4j_admin import DockerNeo4jAdmin
from graphme.adapters.extractors.docx import DocxExtractor
from graphme.adapters.extractors.excel import ExcelExtractor
from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.adapters.extractors.powerpoint import PowerPointExtractor
from graphme.adapters.generation.chat_completions import ChatCompletionsGenerationAdapter
from graphme.adapters.offline import (
    DeterministicGenerationProvider,
    DeterministicVisionProvider,
    HashEmbeddingProvider,
)
from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer
from graphme.adapters.rendering.page_image import PageImageRenderer
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.rendering.visual_binding import RenderedVisualBinder
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.adapters.storage.graph import SQLiteGraphStore
from graphme.adapters.storage.graph_rag import GraphRag, OpenAIBatchEmbeddings
from graphme.adapters.storage.local_neo4j import LOOPBACK, ensure_local_neo4j, own_port
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace, PasswordWanted
from graphme.adapters.vision.chat_completions import ChatCompletionsVLMAdapter
from graphme.adapters.vision.hierarchy_scope import RenderedHierarchyScopeProvider
from graphme.adapters.vision.text_grouping import RenderedUnitGrouper
from graphme.application.corpus_build import BuildReport, StepListener, build_graph
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.embedded_documents import unpack_embedded
from graphme.application.embedding import EmbeddingGenerator
from graphme.application.evaluate import EvaluationService
from graphme.application.grounding import GroundedDocumentBuilder
from graphme.application.index_representation import MultimodalIndexComposer
from graphme.application.indexing import DocumentIndexBuilder
from graphme.application.ingest import DocumentIngestor, documents_at, format_of
from graphme.application.inspect import InspectionService
from graphme.application.page_enrichment import RenderedPageEnricher
from graphme.application.query import QueryService
from graphme.application.relations import RelationReconciler
from graphme.application.semantic_reconciliation import SemanticReconciler
from graphme.config import Settings, load_settings
from graphme.domain.enums import DocumentFormat
from graphme.ports.embedding import EmbeddingProvider
from graphme.ports.generation import GenerationProvider
from graphme.ports.vision import VisionProvider

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Runtime:
    settings: Settings
    store: SQLiteGraphStore
    ingest: DocumentIngestor
    inspect: InspectionService
    query: QueryService
    page_renderer: PageImageRenderer
    evaluate: EvaluationService


def create_store(settings: Settings | None = None) -> SQLiteGraphStore:
    settings = settings or load_settings()
    store = SQLiteGraphStore(settings.database_path)
    store.initialize()
    return store


def chat_endpoint(settings: Settings) -> Endpoint:
    """Where every chat call of these settings goes, with its key -- checked first."""
    _require_chat_key(settings)
    wire = model_endpoint.PROVIDERS[settings.provider].wire
    return Endpoint(wire, settings.base_url, settings.model, settings.api_key)


def _require_chat_key(settings: Settings) -> None:
    """A hosted provider refuses every call without its key: said before the first one."""
    if model_endpoint.KEY_REQUIRED and settings.api_key is None:
        key_env = model_endpoint.PROVIDERS[settings.provider].key_env
        raise ValueError(f"{key_env} is required (GRAPHME_PROVIDER={settings.provider})")


def create_embedder(settings: Settings) -> OpenAIBatchEmbeddings:
    """The provider text embedding the graph space is searched by, at the embedder's own
    address and with its own key."""
    row = model_endpoint.EMBEDDERS.get(settings.embedding_provider)
    if row is None:
        raise ValueError(
            f"GRAPHME_EMBEDDING_PROVIDER={settings.embedding_provider} has no provider "
            "embedder to embed or search with"
        )
    if settings.embedding_key is None:
        hint = (
            "; Anthropic has no embeddings -- or set it to gemini with GEMINI_API_KEY"
            if settings.provider == "anthropic" and settings.embedding_provider == "openai"
            else ""
        )
        raise ValueError(
            f"{row.key_env} is required to embed or search "
            f"(GRAPHME_EMBEDDING_PROVIDER={settings.embedding_provider}{hint})"
        )
    return OpenAIBatchEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_key,
        base_url=row.base_url,
        max_retries=5,
    )


def settings_for_evaluation(
    profile: str, *, settings: Settings | None = None
) -> Settings:
    settings = settings or load_settings()
    if profile == "test":
        return settings.model_copy(
            update={"profile": "test", "embedding_provider": "hash"}
        )
    if profile != "live":
        raise ValueError(f"unsupported evaluation profile: {profile}")
    if settings.profile != "local" or settings.embedding_provider not in model_endpoint.EMBEDDERS:
        raise ValueError("live evaluation requires a provider embedder")
    return settings


def create_runtime(
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> Runtime:
    settings = settings or load_settings()
    store = create_store(settings)
    assets = LocalAssetStore(settings.asset_dir)

    embedding: EmbeddingProvider
    generation: GenerationProvider
    vision: VisionProvider
    if settings.profile == "test" and settings.embedding_provider in {"fake", "hash"}:
        embedding = HashEmbeddingProvider(dimensions=settings.embedding_dimensions)
        generation = DeterministicGenerationProvider()
        vision = DeterministicVisionProvider()
    else:
        endpoint = chat_endpoint(settings)
        embedder = create_embedder(settings)
        if client is None:
            raise ValueError("an HTTP client is required for the local runtime")
        embedding = TextEmbeddingProvider(
            name=settings.embedding_provider,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            embedder=embedder,
        )
        generation = ChatCompletionsGenerationAdapter(
            base_url=endpoint.base_url,
            model=endpoint.model,
            client=client,
            api_key=endpoint.key,
            wire=endpoint.wire,
        )
        vision = create_vision(settings, client)

    query = QueryService(
        store=store,
        embedding_provider=embedding,
        generation_provider=generation,
        settings=settings,
    )
    browser_renderer = create_browser_renderer(settings)
    page_renderer = PageImageRenderer(
        browser_renderer=browser_renderer,
        asset_store=assets,
    )
    # Crops and the hierarchy pass work on pages, whichever thing drew them.
    drawn_pages = PageRenderer(browser=browser_renderer)
    grounding = GroundedDocumentBuilder(
        renderer=page_renderer,
        asset_store=assets,
        vision_provider=vision,
    )
    index_builder = DocumentIndexBuilder(
        grounding=grounding,
        semantic_reconciler=SemanticReconciler(),
        visual_observer=grounding,
        relations=RelationReconciler(provider=vision),
        composer=MultimodalIndexComposer(asset_store=assets),
        embeddings=EmbeddingGenerator(
            generation_provider=generation,
            embedding_provider=embedding,
            store=store,
            limits=model_endpoint.EMBEDDERS.get(settings.embedding_provider),
        ),
        # Off by default: the derived outline costs a model call per sheet, and
        # nothing else builds one.
        hierarchy_mode=settings.hierarchy_mode,
        **graph_parts(
            settings,
            renderer=drawn_pages,
            vision=vision,
            assets=assets,
            derived=settings.hierarchy_mode == "derived",
            unit_grouping=settings.unit_grouping == "page",
        ),
    )
    ingest = DocumentIngestor(
        store=store,
        index_builder=index_builder,
        embedding_provider=embedding.name,
        embedding_model=embedding.model,
        embedding_dimensions=embedding.dimensions,
    )
    return Runtime(
        settings=settings,
        store=store,
        ingest=ingest,
        inspect=InspectionService(store=store),
        page_renderer=page_renderer,
        query=query,
        evaluate=EvaluationService(
            query_service=query,
            expected_profile="test" if settings.profile == "test" else "live",
        ),
    )


def evaluation_profile(suite: Path) -> str:
    return EvaluationService.read_suite(suite).profile


def create_vision(settings: Settings, client: httpx.Client) -> ChatCompletionsVLMAdapter:
    """The page-reading model: one adapter shape for every pass that reads a page."""
    endpoint = chat_endpoint(settings)
    return ChatCompletionsVLMAdapter(
        base_url=endpoint.base_url, model=endpoint.model, client=client,
        api_key=endpoint.key, wire=endpoint.wire,
    )


def create_browser_renderer(settings: Settings) -> BrowserOfficeRenderer:
    """PDF rasterises directly; every OOXML format goes through the browser bundle."""
    return BrowserOfficeRenderer(
        viewer_directory=settings.viewer_directory,
        chrome_executable=settings.chrome_executable,
        timeout_seconds=settings.render_timeout_seconds,
    )


def graph_parts(
    settings: Settings,
    *,
    renderer: Any,
    vision: VisionProvider,
    assets: Any,
    derived: bool,
    unit_grouping: bool,
    limiter: AbstractContextManager[Any] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    """What turning a document into its graph needs, wired one way for every caller.

    `derived` reads the outline off the page, and with it the figures and, after the
    outline, what visuals show and which documents the pages name; without it there is
    none of these. `unit_grouping` joins printed pieces into paragraphs before the
    outline. `limiter` and `workers` are for a caller reading several documents at once.
    """
    return {
        "extractors": {
            DocumentFormat.XLSX: ExcelExtractor(asset_store=assets),
            DocumentFormat.PDF: PDFExtractor(asset_store=assets),
            DocumentFormat.PPTX: PowerPointExtractor(asset_store=assets),
            DocumentFormat.DOCX: DocxExtractor(asset_store=assets),
        },
        "visual_binder": RenderedVisualBinder(renderer=renderer, asset_store=assets),
        "hierarchy_provider": (
            RenderedHierarchyScopeProvider(
                renderer=renderer,
                vision=vision,
                asset_store=assets,
                readings=settings.hierarchy_readings,
                limiter=limiter,
                workers=workers,
            )
            if derived
            else None
        ),
        # Between the figures and the outline: what a figure swallowed is not a paragraph,
        # and an outline drawn over half a paragraph places half a paragraph.
        "unit_grouper": (
            RenderedUnitGrouper(renderer=renderer, vision=vision, limiter=limiter)
            if derived and unit_grouping
            else None
        ),
        # Captions and named documents are read off the same rendering the outline is, so
        # a document read without a model gets neither. A named document stands as an empty
        # node; which indexed document it is is said across the corpus
        # (`application/corpus_links.py`), whatever order they arrive in.
        "enricher": (
            RenderedPageEnricher(
                renderer=renderer, vision=vision, asset_store=assets,
                workers=workers, limiter=limiter,
            )
            if derived
            else None
        ),
    }


def create_graph_builder(settings: Settings, **parts: Any) -> DocumentGraphBuilder:
    """A document into its graph -- the same reading the index, the space and the demo get."""
    return DocumentGraphBuilder(**graph_parts(settings, **parts))


def create_graph_space(settings: Settings) -> Neo4jGraphSpace:
    """The one graph every document of a corpus is put in -- on this machine, a Neo4j
    started for it when none answers there."""
    memory = (settings.neo4j_heap, settings.neo4j_pagecache)
    ensure_local_neo4j(settings.neo4j_uri, settings.neo4j_admin_image, memory)
    try:
        return Neo4jGraphSpace.from_settings(settings)
    except PasswordWanted:
        # Someone else's Neo4j with a password on this machine's port (Neo4j Desktop, brew):
        # with no password given, it was never meant -- ours runs on a port of its own.
        if settings.neo4j_password or urlsplit(settings.neo4j_uri).hostname not in LOOPBACK:
            raise
    uri = f"bolt://127.0.0.1:{own_port()}"
    _log.warning("%s의 Neo4j는 비밀번호를 요구해서, 이 앱의 Neo4j를 %s에서 씁니다",
                 settings.neo4j_uri, uri)
    ensure_local_neo4j(uri, settings.neo4j_admin_image, memory)
    return Neo4jGraphSpace.from_settings(settings.model_copy(update={"neo4j_uri": uri}))


def create_neo4j_admin(settings: Settings) -> DockerNeo4jAdmin:
    """`neo4j-admin` for a `.dump` and an Aura upload, from the configured Neo4j image."""
    return DockerNeo4jAdmin(settings.neo4j_admin_image)


def create_graph_rag(
    settings: Settings, space: Neo4jGraphSpace, *, client: httpx.Client | None = None
) -> GraphRag:
    """Questions over the graph space, on its own connection: the embedder's vectors to find
    passages by, and the same model endpoint every other call goes to for the answer."""
    embedder = create_embedder(settings)
    return GraphRag(
        space,
        embedder=embedder,
        endpoint=chat_endpoint(settings),
        client=client or httpx.Client(timeout=600.0),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        relevance_floor=settings.relevance_floor,
        dense_weight=settings.hybrid_dense_weight,
        rrf_k=settings.rrf_k,
        assets=LocalAssetStore(settings.asset_dir),
    )


def corpus_graph_builder(settings: Settings, client: httpx.Client) -> DocumentGraphBuilder:
    """The graph builder every document of a corpus is read with: rendered, its outline and
    paragraphs read off the page, its visuals described."""
    return create_graph_builder(
        settings,
        renderer=PageRenderer(browser=create_browser_renderer(settings)),
        vision=create_vision(settings, client),
        assets=LocalAssetStore(settings.asset_dir),
        derived=True,
        unit_grouping=True,
    )


def build_corpus_graph(
    path: Path,
    *,
    client: httpx.Client,
    settings: Settings | None = None,
    on_step: StepListener | None = None,
    again: bool = False,
    space: Neo4jGraphSpace | None = None,
) -> BuildReport:
    """Every document at a path -- a folder's, or one file -- read in full, put in the graph
    space, and joined there.

    The graph only: nothing is embedded here, so a corpus is built without an embedding
    provider, and what retrieval needs is added to the space on its own afterwards. A
    `space` passed in is the caller's to close.
    """
    settings = settings or load_settings()
    documents = documents_at(path)
    graph = corpus_graph_builder(settings, client)
    own = space is None
    space = space if space is not None else create_graph_space(settings)
    try:
        return build_graph(
            unpack_embedded(documents, settings.data_dir / "embedded"),
            process=lambda source: graph.build(source, format_of(source)),
            space=space,
            on_step=on_step,
            again=again,
        )
    finally:
        if own:
            space.close()

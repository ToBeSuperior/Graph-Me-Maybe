from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx

from graphme import model_endpoint
from graphme.adapters.chat import Endpoint
from graphme.adapters.embedding import OpenAIBatchEmbeddings
from graphme.adapters.export.neo4j_admin import LocalNeo4jAdmin
from graphme.adapters.extractors.docx import DocxExtractor
from graphme.adapters.extractors.excel import ExcelExtractor
from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.adapters.extractors.powerpoint import PowerPointExtractor
from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.rendering.visual_binding import RenderedVisualBinder
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.adapters.storage.graph_rag import GraphRag
from graphme.adapters.storage.local_neo4j import USER, ensure_local_neo4j, install
from graphme.adapters.storage.local_neo4j import password as neo4j_password
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.adapters.vision.chat_completions import ChatCompletionsVLMAdapter
from graphme.adapters.vision.hierarchy_scope import RenderedHierarchyScopeProvider
from graphme.adapters.vision.text_grouping import RenderedUnitGrouper
from graphme.application.corpus_build import BuildReport, StepListener, build_graph
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.embedded_documents import unpack_embedded
from graphme.application.ingest import documents_at, format_of
from graphme.application.page_enrichment import RenderedPageEnricher
from graphme.application.progress import Progress
from graphme.config import Settings, app_home, load_settings
from graphme.domain.enums import DocumentFormat
from graphme.ports.vision import VisionProvider

_log = logging.getLogger(__name__)


def chat_endpoint(settings: Settings) -> Endpoint:
    """Where every chat call of these settings goes, with its key -- checked first."""
    _require_chat_key(settings)
    wire = model_endpoint.PROVIDERS[settings.provider].wire
    return Endpoint(wire, settings.base_url, settings.model, settings.api_key)


def _require_chat_key(settings: Settings) -> None:
    """A hosted provider refuses every call without its key: said before the first one."""
    if model_endpoint.KEY_REQUIRED and settings.api_key is None:
        key_env = model_endpoint.PROVIDERS[settings.provider].key_env
        raise ValueError(
            f"{key_env} is required (GRAPHME_PROVIDER={settings.provider}) -- "
            "put it in the app's Manage › Keys and model")


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
            f"(GRAPHME_EMBEDDING_PROVIDER={settings.embedding_provider}{hint}) -- "
            "put it in the app's Manage › Keys and model"
        )
    return OpenAIBatchEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_key,
        base_url=row.base_url,
        max_retries=5,
    )


def create_vision(
    settings: Settings, client: httpx.Client, *,
    limiter: AbstractContextManager[Any] | None = None,
) -> ChatCompletionsVLMAdapter:
    """The page-reading model: one adapter shape for every pass that reads a page. `limiter`
    is held around each of its requests, for a caller reading several documents at once."""
    endpoint = chat_endpoint(settings)
    return ChatCompletionsVLMAdapter(
        base_url=endpoint.base_url, model=endpoint.model, client=client,
        api_key=endpoint.key, wire=endpoint.wire, limiter=limiter,
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
    workers: int = 1,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """What turning a document into its graph needs, wired one way for every caller.

    The outline is read off the page, and with it the figures, the paragraphs before the
    outline and, after it, what visuals show and which documents the pages name.
    `workers` is how many pages are asked at once; `progress` counts every question asked.
    """
    return {
        "extractors": {
            DocumentFormat.XLSX: ExcelExtractor(asset_store=assets),
            DocumentFormat.PDF: PDFExtractor(asset_store=assets),
            DocumentFormat.PPTX: PowerPointExtractor(asset_store=assets),
            DocumentFormat.DOCX: DocxExtractor(asset_store=assets),
        },
        "visual_binder": RenderedVisualBinder(renderer=renderer, asset_store=assets),
        "hierarchy_provider": RenderedHierarchyScopeProvider(
            renderer=renderer,
            vision=vision,
            asset_store=assets,
            readings=settings.hierarchy_readings,
            workers=workers,
            progress=progress,
        ),
        # Between the figures and the outline: what a figure swallowed is not a paragraph,
        # and an outline drawn over half a paragraph places half a paragraph.
        "unit_grouper": RenderedUnitGrouper(renderer=renderer, vision=vision, progress=progress),
        # Captions and named documents are read off the same rendering the outline is. A
        # named document stands as an empty node; which indexed document it is is said across
        # the corpus (`application/corpus_links.py`), whatever order they arrive in.
        "enricher": RenderedPageEnricher(
            renderer=renderer, vision=vision, asset_store=assets,
            workers=workers, progress=progress,
        ),
    }


def create_graph_builder(settings: Settings, **parts: Any) -> DocumentGraphBuilder:
    """A document into its graph -- the same reading the space and the demo get."""
    return DocumentGraphBuilder(**graph_parts(settings, **parts))


def create_graph_space(settings: Settings) -> Neo4jGraphSpace:
    """The one graph every document of a corpus is put in: the installed app's own Neo4j --
    started, and fetched first if need be -- or the one GRAPHME_NEO4J_URI names."""
    if settings.local_neo4j is not None:
        uri = ensure_local_neo4j(settings.local_neo4j,
                                 (settings.neo4j_heap, settings.neo4j_pagecache))
        settings = settings.model_copy(update={
            "neo4j_uri": uri, "neo4j_user": USER,
            "neo4j_password": neo4j_password(settings.local_neo4j)})
    return Neo4jGraphSpace.from_settings(settings)


def create_neo4j_admin(settings: Settings) -> LocalNeo4jAdmin:
    """`neo4j-admin` for a `.dump` and an Aura upload, from the app's own Neo4j -- fetched
    into the app's home first if it is not there (a checkout's export)."""
    return LocalNeo4jAdmin(install(settings.local_neo4j or app_home()))


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

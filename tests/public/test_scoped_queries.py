"""Every statement the storage adapters send is held to one workspace: a recording driver runs
each public method of the space, the RAG, the hybrid search and the likeness over invented
data, and no node pattern may lack the workspace's label; the schema is the workspace's own."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
import neo4j

from graphme.adapters.chat import Endpoint
from graphme.adapters.export.neo4j import records_cypher
from graphme.adapters.export.neo4j_import import CorpusRows, write_import_bundle
from graphme.adapters.storage.graph_rag import GraphRag
from graphme.adapters.storage.hybrid_search import _OnceRetriever, documents_with_message_ids
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.adapters.storage.similar_documents import SimilarDocuments
from graphme.adapters.storage.workspace_scope import WorkspaceScope, unscoped_patterns
from graphme.application.graph_chunks import Chunk
from tests.fakes.documents import named_document

SCOPE = WorkspaceScope("4a8cf515")
_SCHEMA = re.compile(r"^\s*(CREATE|DROP|SHOW)\b|db\.awaitIndex", re.IGNORECASE)

_PASSAGE = {
    "id": "ab:0", "score": 0.9, "document_id": "ab", "file": "invented.pdf",
    "text": "An invented passage.", "members": ["ab-p"], "quote_of": None, "modified": None,
    "opening": None, "pages": [], "carriers": [], "linked": [],
}
ANSWERS: dict[str, list[dict[str, Any]]] = {
    # embed: one passage to embed, one document to rate, one to relate
    "c.text AS text": [{"document": "ab", "id": "ab:0", "text": "An invented passage."}],
    "rated < passages": [{"document": "ab", "passages": 1}],
    "AS vector": [{"document": "ab", "id": "ab:0", "vector": [0.1, 0.2, 0.3]}],
    "coalesce(d.similarity_rule": [{"id": "ab"}],
    "d.label AS name": [{"id": "ab", "name": "invented.pdf"}],
    "queryNodes($vector_index_name": [_PASSAGE],
    "count(d) AS n": [{"n": 0}],
}


class _Recording(neo4j.Driver):
    """A driver that keeps every statement and its parameters, and answers from ANSWERS."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def _answer(self, query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.sent.append((query, parameters))
        return next((rows for key, rows in ANSWERS.items() if key in query), [])

    def execute_query(self, query: str, parameters: dict[str, Any] | None = None,
                      **kw: Any) -> neo4j.EagerResult:
        given = {**(parameters or {}), **{k: v for k, v in kw.items() if not k.endswith("_")}}
        return neo4j.EagerResult(self._answer(query, given), None, [])  # type: ignore[arg-type]

    def session(self, **_: Any) -> _Session:
        return _Session(self)


class _Session:
    def __init__(self, driver: _Recording) -> None:
        self.driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, parameters: dict[str, Any] | None = None, **kw: Any) -> Any:
        return iter(self.driver._answer(query, {**(parameters or {}), **kw}))

    def execute_write(self, work: Any, *args: Any) -> Any:
        return work(self, *args)


class _Embedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


def _model(request: httpx.Request) -> httpx.Response:
    if b"question_split" in request.content:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"queries": ["an invented question"]})}}]})
    return httpx.Response(200, json={"choices": [{"message": {"content": "Invented."}}]})


def _ran_everything(driver: _Recording) -> None:
    space = Neo4jGraphSpace(driver, scope=SCOPE)  # type: ignore[arg-type]
    space.put_document(named_document("ab", "invented.pdf", names=("Another invented",)))
    space.read_from("invented.pdf")
    space.relocate("0" * 64, "invented.pdf")
    space.source_path("ab")
    space.delete_documents(["ab"])
    space.carried_only_by(["ab"])
    space.identities()
    space.mentions()
    space.reset()
    space.files(["ab"])
    space.crop_hashes()
    space.contents(["0" * 64])
    space.unlinked("rule")
    space.mark_linked(["ab"], "rule")
    space.put_links([], of=["ab-ext0"])
    space.documents_to_cut(None, rule="rule")
    space.units("ab")
    space.put_chunks("ab", [Chunk(text="An invented passage.", member_ids=("ab-p",))],
                     rule="rule")
    space.document_map()
    space.similar_links()
    space.document_graph("ab")
    space.chunk_spans("ab")
    rows = space.corpus_rows()
    list(rows.nodes()), list(rows.relationships())
    space.corpus()

    rag = GraphRag(
        space, embedder=_Embedder(),  # type: ignore[arg-type]
        endpoint=Endpoint("chat_completions", "https://chat.test/v1", "chat-test", None),  # type: ignore[arg-type]
        client=httpx.Client(transport=httpx.MockTransport(_model)), model="m", dimensions=3,
        relevance_floor=0.0, dense_weight=0.6, rrf_k=60, scope=SCOPE,
        assets=object(),  # type: ignore[arg-type]
    )
    rag.embed()
    retriever = object.__new__(_OnceRetriever)
    retriever.driver = driver  # type: ignore[assignment]
    retriever.embedder = _Embedder()  # type: ignore[assignment]
    retriever.text_only, retriever.neo4j_database = False, None
    retriever.dense_weight, retriever.floor, retriever.rrf_k = 0.6, 0.0, 60
    retriever.vector_index_name, retriever.fulltext_index_name = (
        SCOPE.vector_index, SCOPE.text_index)
    retriever.retrieval_query = rag._retrieval_query()
    retriever.scope = SCOPE
    rag._rag = retriever
    rag.ask("an invented question", documents=1)
    rag.find("an invented question", documents=1)
    documents_with_message_ids(driver, ["<a@invented.test>"], scope=SCOPE)  # type: ignore[arg-type]
    SimilarDocuments(driver, index=SCOPE.vector_index, vector_property="embedding",  # type: ignore[arg-type]
                     scope=SCOPE).relate(None)


def test_every_statement_names_the_workspace() -> None:
    driver = _Recording()
    _ran_everything(driver)

    reads = [query for query, _ in driver.sent if not _SCHEMA.search(query)]
    assert len(reads) > 40
    assert [(q, unscoped_patterns(q)) for q in reads if unscoped_patterns(q)] == []
    # A pattern naming no label is reached only through one that does.
    assert [q for q in reads if re.search(r"MATCH \(\w+ \{", q)] == []


def test_the_schema_is_the_workspaces_own() -> None:
    driver = _Recording()
    _ran_everything(driver)

    schema = [(q, p) for q, p in driver.sent if _SCHEMA.search(q)]
    created = [q for q, _ in schema if q.lstrip().upper().startswith("CREATE")]
    labels = {label for q in created for label in re.findall(r"FOR \(\w+:(\w+)\)", q)}
    assert labels == {SCOPE.node_label, SCOPE.chunk_label}
    names = [m for q in created for m in re.findall(r"(?:CONSTRAINT|INDEX) (\w+) IF", q)]
    assert names and all(name.endswith("_4a8cf515") for name in names)
    assert any(f"INDEX {SCOPE.text_index} " in q for q in created)
    assert any(p.get("name") == SCOPE.vector_index for q, p in schema if "VECTOR INDEX" in q)
    asked = [p for q, p in driver.sent if "queryNodes" in q or "SHOW" in q]
    for given in asked:
        for key in ("index", "name", "vector_index_name", "fulltext_index_name"):
            if key in given:
                assert given[key] in (SCOPE.vector_index, SCOPE.text_index), (key, given)
        if "label" in given:
            assert given["label"] == SCOPE.chunk_label


def test_an_export_writes_into_the_workspace(tmp_path: Any) -> None:
    driver = _Recording()
    space = Neo4jGraphSpace(driver, scope=SCOPE)  # type: ignore[arg-type]
    records = {"nodes": [{"id": "ab", "labels": ["DocNode", "Document"], "properties": {}},
                         {"id": "ab-p", "labels": ["DocNode", "TextChunk"], "properties": {}}],
               "relationships": [{"id": "r", "type": "PARENT_OF", "start": "ab",
                                  "end": "ab-p", "properties": {}}]}
    script = records_cypher(records, "// invented", scope=SCOPE)
    assert unscoped_patterns(script.split("\n", 2)[2]) == []
    assert f"FOR (n:{SCOPE.node_label}) REQUIRE n.id IS UNIQUE" in script

    write_import_bundle(space.corpus_rows(), tmp_path)
    schema = (tmp_path / "schema.cypher").read_text(encoding="utf-8")
    assert "(n:DocNode)" not in schema and "(c:Chunk)" not in schema
    assert f"FOR (c:{SCOPE.chunk_label}) REQUIRE c.id IS UNIQUE" in schema

    rows = CorpusRows(nodes=lambda: [("DocNode;Document", "ab", {"label": "x"}),
                                     ("Chunk", "ab:0", {"text": "x"})],
                      relationships=lambda: [], indexes=[], documents=1, scope=SCOPE)
    written = write_import_bundle(rows, tmp_path / "bundle")
    cells = "".join(path.read_text(encoding="utf-8") for path in written
                    if path.name.startswith("nodes_"))
    assert f"DocNode;Document;{SCOPE.node_label}" in cells
    assert f"Chunk;{SCOPE.chunk_label}" in cells

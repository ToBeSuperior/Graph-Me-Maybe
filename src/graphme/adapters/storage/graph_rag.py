"""Questions over the graph space, answered with neo4j-graphrag.

The graph space holds what `build-graph` put there, and keeps the passages a question is
searched by (`Neo4jGraphSpace.put_chunks`). This adds the rest on top of it.

`embed` cuts every document not yet cut by the current rule (`graph_chunks.CHUNK_RULE`),
embeds every passage not yet embedded with this model, and keeps one vector index over them
at the model's size -- an index left at another size would hold none of the new vectors.

`ask` finds passages by hybrid search over Neo4j's two indexes -- the vector index (ANN) for
what a question means, and the fulltext index (BM25) for the words it names exactly, a
document number or a file name, which a vector ranks below passages that only sound alike --
the two rankings fused by weighted reciprocal rank fusion (`_OnceRetriever.fused`). Then it
walks the links between documents: the found passage's document is hop 1,
every document it cites or is cited by is hop 2, and every document linked to those is hop
3 -- each with its passages nearest the question. The model is handed exactly
that, in one call; nothing here reasons from one hop to the next.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import neo4j
from neo4j_graphrag.indexes import create_vector_index
from neo4j_graphrag.retrievers import HybridCypherRetriever
from neo4j_graphrag.types import RetrieverResultItem
from pydantic import BaseModel, Field

from graphme.adapters.chat import Endpoint, complete, image_part, is_transient
from graphme.adapters.embedding import BatchEmbedder
from graphme.adapters.export.neo4j import CHUNK_LABEL, NODE_LABEL
from graphme.adapters.storage.neo4j_space import CHUNK_TEXT_INDEX, Neo4jGraphSpace, checked_name
from graphme.adapters.storage.similar_documents import SimilarDocuments
from graphme.adapters.strict_schema import strict_response_schema
from graphme.application.corpus_links import document_numbers
from graphme.application.graph_chunks import CHUNK_RULE, chunks
from graphme.application.passage_scores import IMPORTANCE_RULE, centroid, importance
from graphme.application.xlsx_visual_binding import CROP_ASSET_KEY, read_crop
from graphme.ports.storage import AssetStore

VECTOR_INDEX = "chunk_embedding"
VECTOR_PROPERTY = "embedding"
"""Where each passage keeps its vector; `_model` and `_centroid` beside it."""

_log = logging.getLogger(__name__)

PASSAGE_CHARS = 6000
"""A last guard before a passage is embedded; the chunker already keeps them to a budget."""
BATCH = 32
RATE_PASSAGES = 2000
"""About how many passages are rated together: their vectors are held at once."""
LINKED_PASSAGES = {2: 2, 3: 1}
"""Passages of a linked document brought along, by hop -- the ones nearest the question."""
LINK_CANDIDATES = 6
"""Of the documents linked to one, how many are read passage by passage -- those nearest the
question as a whole (their centroids): a document linked to dozens is not read through all
of them, and a few more than LINKED_DOCUMENTS leave room for one whose best passage is
nearer than its whole."""
LINKED_DOCUMENTS = 4
"""Linked documents a found passage brings along at each hop, the ones nearest the question:
a much-cited paper is linked to dozens, and all of them is a context nobody answers from."""
ANSWER_PICTURES = 16
"""Most crops one answer is sent: two a passage at the default top_k. A passage of a slide
full of icons would otherwise send each of them."""
PICTURES_FOLLOW = (
    "\n\nThe tables, pictures and charts the context describes follow, each named by the file "
    "it is in. Read them where the description leaves something out, and cite their file."
)
MAX_TOP_K = 20
"""More passages than this is a prompt too long to answer from, not a better answer."""

SYSTEM = (
    "Answer the question using only the provided context. Answer in the language the "
    "question is written in, whatever language the context is in; when the question shows no "
    "language of its own -- only numbers, codes or names -- answer in English. Write the "
    "whole answer in "
    "that one language: when it is Korean, write Korean "
    "(Hangul) throughout, and never use Chinese characters (Hanzi) or Japanese, not even for "
    "a single word; keep a term in its original form only where the context writes it so, "
    "such as a part number, a unit or a standard's name. End each statement with the "
    "[file name] of the context it rests on. If the "
    "context does not hold the answer, say that it cannot be found in the context, and "
    "cite nothing. If the context does not establish what the question presupposes -- the "
    "entity, period, group or document it names -- say that the question cannot be answered "
    "from the documents rather than answering about something similar: a figure for another "
    "group, year or document is not an answer about the one asked for. A document applies "
    "to what it names -- a product, a model, a project, a "
    "period: where the context's documents are about different things, say which applies "
    "to what the question asks, and do not carry a rule from one to another."
)
SPLIT_SYSTEM = (
    "Split the user's question into the separate things it asks for, each written as a "
    "search query of its own, in the question's language. Keep every name, model, number, "
    "year and term the question uses in each query that is about it -- a query is searched "
    "alone and must say what it is about. A question that asks for one thing comes back as "
    "that one query, as written: never several wordings, synonyms or guesses at causes of "
    "the same thing -- each query must ask for something the others do not. At most three "
    "queries. Return only JSON matching the schema."
)
"""How a question asking several things -- a problem and how competitors handled it -- is
cut into one search each: one search for both finds what the nearer part is about."""
MAX_QUERIES = 3


ASK_RETRY_DELAYS = (2.0, 5.0)
"""Seconds to wait before asking again when the endpoint turned a question away for now
(`TRANSIENT_STATUS`, a timeout, a dropped connection): someone is waiting on the answer, so
this is short, unlike a page reading's schedule."""


class _Split(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES)


TEMPLATE = """Context:
{context}

Question:
{query_text}

Answer:"""


class GraphNotEmbedded(RuntimeError):
    """A question was asked of a space nothing was embedded in yet."""


@dataclass(frozen=True, slots=True)
class LinkedPassage:
    """A passage of a linked document, brought along because it is near the question."""

    id: str
    score: float
    """Its similarity to the question."""
    members: list[str]
    text: str


@dataclass(frozen=True, slots=True)
class LinkedDocument:
    """A document reached from a found passage's document by following the links between
    documents: hop 2 is linked to that document, hop 3 to a hop-2 document."""

    document_id: str
    file: str
    hop: int
    via: str
    """The document it was reached from."""
    direction: str
    """How `via` is linked to it: "cites" it, is "cited_by" it, or is "similar" to it."""
    passages: list[LinkedPassage]
    modified: float | None = None
    """When its file was last changed (`Document.modified_at`)."""


@dataclass(frozen=True, slots=True)
class GraphSource:
    """A passage an answer was given, and the documents the graph reached from its own:
    the passage's document is hop 1, the documents linked to it hop 2, theirs hop 3."""

    id: str
    score: float
    document_id: str
    file: str
    members: list[str]
    """The units it covers, in reading order."""
    context: str
    """The passage exactly as the model saw it, with what the graph brought along."""
    linked: list[LinkedDocument] = field(default_factory=list)
    modified: float | None = None
    """When its document's file was last changed (`Document.modified_at`)."""


@dataclass(frozen=True, slots=True)
class GraphAnswer:
    text: str
    sources: list[GraphSource]


class GraphRag:
    def __init__(
        self,
        space: Neo4jGraphSpace,
        *,
        embedder: BatchEmbedder,
        endpoint: Endpoint,
        client: httpx.Client,
        model: str,
        dimensions: int,
        index: str = VECTOR_INDEX,
        vector_property: str = VECTOR_PROPERTY,
        relevance_floor: float,
        dense_weight: float,
        rrf_k: int,
        assets: AssetStore | None = None,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._space = space
        self._wait = wait
        self._assets = assets
        """Where the crops a passage's pictures name are read from; none, and an answer is
        given the passages' words alone."""
        self._split: dict[str, tuple[str, ...]] = {}
        self._lock = threading.Lock()
        self._flights: dict[object, Future[Any]] = {}
        """What is being worked out right now, by what it is for (`_shared`)."""
        self._embedder = embedder
        self._endpoint = endpoint
        self._client = client
        self._model = model
        self._dimensions = dimensions
        # Another index and property are a run of its own beside the space's (the tests).
        self._index = checked_name(index)
        self._text_index = CHUNK_TEXT_INDEX
        self._floor = relevance_floor
        self._dense_weight = dense_weight
        self._rrf_k = rrf_k
        self._vector = checked_name(vector_property)
        self._model_property = checked_name(f"{vector_property}_model")
        self._centroid = checked_name(f"{vector_property}_centroid")
        """On each document: the mean of its passages' vectors, what it is mostly about."""
        self._similar = SimilarDocuments(space.driver, index=index,
                                         vector_property=vector_property)
        self._rag: _OnceRetriever | None = None
        """Made on the first question: building a retriever asks the database twice."""

    @property
    def _driver(self) -> neo4j.Driver:
        return self._space.driver

    def embed(
        self,
        document_ids: Sequence[str] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Cut every document not yet cut, embed every passage not yet embedded with this
        model; how many were embedded."""
        for document_id, name in self._space.documents_to_cut(
            document_ids, rule=CHUNK_RULE
        ).items():
            self._space.put_chunks(
                document_id, chunks(name, self._space.units(document_id)), rule=CHUNK_RULE
            )
        self._ensure_index()
        records, _, _ = self._driver.execute_query(
            f"""
            MATCH (c:{CHUNK_LABEL})
            WHERE ($documents IS NULL OR c.document_id IN $documents)
              AND (c.`{self._vector}` IS NULL OR c.`{self._model_property}` <> $model)
            RETURN c.document_id AS document, c.id AS id, c.text AS text
            """,
            documents=list(document_ids) if document_ids is not None else None,
            model=self._model,
        )
        rows = [(record["document"], record["id"], record["text"][:PASSAGE_CHARS])
                for record in records]
        for start in range(0, len(rows), BATCH):
            batch = rows[start : start + BATCH]
            vectors = self._embedder.embed_texts([text for *_, text in batch])
            self._driver.execute_query(
                f"""
                UNWIND $rows AS row
                MATCH (c:{CHUNK_LABEL} {{id: row.id}})
                CALL db.create.setNodeVectorProperty(c, $property, row.vector)
                // Rated from the vector it had: rated again, even if this run stops here.
                SET c.`{self._model_property}` = $model
                REMOVE c.importance_rule
                """,
                rows=[{"id": i, "vector": v} for (_, i, _), v in zip(batch, vectors, strict=True)],
                property=self._vector,
                model=self._model,
            )
            if on_progress is not None:
                on_progress(start + len(batch), len(rows))
        # How alike two documents are weighs their passages by importance: measured again
        # whenever that is -- every document just embedded is rated again.
        self._rate(document_ids)
        self._similar.relate(document_ids)
        return len(rows)

    def _rate(self, document_ids: Sequence[str] | None) -> None:
        """Each passage's importance in its document (`importance`) and each document's
        centroid, for every document with a passage not rated by this rule -- embedding a
        passage takes its rating away. Read and written about RATE_PASSAGES passages at a
        time: a space's vectors are more than a machine holds."""
        records, _, _ = self._driver.execute_query(
            f"""
            MATCH (c:{CHUNK_LABEL})
            WHERE ($documents IS NULL OR c.document_id IN $documents)
              AND c.`{self._vector}` IS NOT NULL
            WITH c.document_id AS document, count(c) AS passages,
                 count(CASE WHEN c.importance_rule = $rule THEN 1 END) AS rated
            WHERE rated < passages
            RETURN document, passages ORDER BY document
            """,
            documents=list(document_ids) if document_ids is not None else None,
            rule=IMPORTANCE_RULE,
        )
        batch: list[str] = []
        held = 0
        for record in records:
            batch.append(record["document"])
            held += record["passages"]
            if held >= RATE_PASSAGES:
                self._rate_batch(batch)
                batch, held = [], 0
        if batch:
            self._rate_batch(batch)

    def _rate_batch(self, documents: list[str]) -> None:
        records, _, _ = self._driver.execute_query(
            f"""
            MATCH (c:{CHUNK_LABEL}) WHERE c.document_id IN $documents
              AND c.`{self._vector}` IS NOT NULL
            RETURN c.document_id AS document, c.id AS id, c.`{self._vector}` AS vector
            ORDER BY document, id
            """,
            documents=documents,
        )
        by_document: dict[str, list[tuple[str, list[float]]]] = {}
        for record in records:
            by_document.setdefault(record["document"], []).append((record["id"], record["vector"]))
        rated = [
            {"id": chunk_id, "importance": score}
            for passages in by_document.values()
            for (chunk_id, _), score in zip(
                passages, importance([vector for _, vector in passages]), strict=True)
        ]
        centres = [{"document": document, "centroid": centroid([v for _, v in passages])}
                   for document, passages in by_document.items()]

        def write(tx: neo4j.ManagedTransaction) -> None:
            # One transaction: rated passages and the centroid -- and the likeness that has to
            # be measured again from them -- go in together or not at all.
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (c:{CHUNK_LABEL} {{id: row.id}})
                SET c.importance = row.importance, c.importance_rule = $rule
                """,
                rows=rated, rule=IMPORTANCE_RULE,
            )
            tx.run(
                """
                UNWIND $rows AS row
                MATCH (d:Document {id: row.document})
                CALL db.create.setNodeVectorProperty(d, $property, row.centroid)
                REMOVE d.similarity_rule
                """,
                rows=centres, property=self._centroid,
            )

        with self._driver.session() as session:
            session.execute_write(write)

    def ask(self, question: str, *, top_k: int) -> GraphAnswer:
        _checked(top_k)
        items = [_item(record) for record in _once(self._found(question, top_k))]
        if not items:
            # Nothing near the question: no passage to answer from, and no model call for it.
            return GraphAnswer(text=_nothing_found(question), sources=[])
        prompt = _prompt(question, "\n".join(item.content for item in items))
        # A graph_chunk holding a table, a picture or a chart says it in words a model wrote
        # once; the picture goes too, so the answer reads it, not the description.
        pictures = self._pictures(items)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt + (PICTURES_FOLLOW if pictures else "")}]
        for label, png in pictures:
            content += [{"type": "text", "text": label}, image_part(png)]
        text = self._complete(system=SYSTEM, content=content)
        return GraphAnswer(text=text, sources=[_source(item) for item in items])

    def _pictures(self, items: Sequence[RetrieverResultItem]) -> list[tuple[str, bytes]]:
        """The crops of the tables, pictures and charts the found passages hold, in the order
        the passages were found, each named by its file and label -- at most
        ANSWER_PICTURES of them."""
        if self._assets is None:
            return []
        members = list(dict.fromkeys(
            m for item in items for m in (item.metadata or {}).get("members", [])))
        records, _, _ = self._space.driver.execute_query(
            _PICTURES, ids=members, key=f"meta_{CROP_ASSET_KEY}",
            routing_=neo4j.RoutingControl.READ,
        )
        by_id = {record["id"]: record for record in records}
        pictures: list[tuple[str, bytes]] = []
        for member in members:
            record = by_id.get(member)
            if record is None:
                continue
            # A crop not kept is a picture the passage's words still describe.
            if (png := read_crop(record["crop"], self._assets)) is None:
                continue
            pictures.append((f"Picture in [{_composed(record['file'])}]: {record['label']}", png))
            if len(pictures) == ANSWER_PICTURES:
                break
        return pictures

    def find(self, question: str, *, top_k: int) -> list[GraphSource]:
        """The passages a question finds and the documents linked to theirs, with no answer
        written -- what the Q folder page shows: as `ask` reads them, but with
        LINKED_DOCUMENTS hop-3 documents under each hop-2 one rather than in all."""
        _checked(top_k)
        found = self._found(question, top_k)
        return [_source(_item(record)) for record in _once(found, each_folder=True)]

    def _found(self, question: str, top_k: int) -> list[neo4j.Record]:
        """The fused search for each thing the question asks (`_queries`), the passages taken
        from each search in turn, best first, each once, top_k in all. An answer and its
        folder page ask side by side: the search runs once for both."""
        def search() -> list[neo4j.Record]:
            retriever = self._retriever()
            searches = [retriever.fused(query, top_k) for query in self._queries(question)]
            found: dict[str, neo4j.Record] = {}
            for rank in range(top_k):
                for one in searches:
                    if rank < len(one) and len(found) < top_k:
                        found.setdefault(one[rank]["id"], one[rank])
            return list(found.values())

        return cast(list[neo4j.Record], self._shared(("found", question, top_k), search))

    def _queries(self, question: str) -> tuple[str, ...]:
        """What the question asks, one search query each (`SPLIT_SYSTEM`) -- or the question
        itself, where the model asks it as one thing or gives no usable answer. The split is
        made once for a question, however many ask at the same time."""
        with self._lock:
            if question in self._split:
                return self._split[question]
        return cast(tuple[str, ...], self._shared(("split", question),
                                                  lambda: self._split_question(question)))

    def _complete(self, **ask: Any) -> str:
        """`complete`, asked again on ASK_RETRY_DELAYS while the endpoint says "not now"."""
        for delay in ASK_RETRY_DELAYS:
            try:
                return complete(self._client, self._endpoint, **ask)
            except httpx.HTTPError as error:
                if not is_transient(error):
                    raise
            self._wait(delay)
        return complete(self._client, self._endpoint, **ask)

    def _split_question(self, question: str) -> tuple[str, ...]:
        try:
            reply = self._complete(system=SPLIT_SYSTEM, content=question,
                                   schema=strict_response_schema(_Split), name="question_split")
            written = _Split.model_validate_json(reply).queries
        except Exception as error:  # noqa: BLE001 -- unsplit is how every question was asked
            _log.warning("question not split -- %s", str(error)[:200])
            return (question,)  # not kept: the next ask tries the model again
        parts = tuple(dict.fromkeys(part for q in written if (part := q.strip())))
        queries = parts if len(parts) > 1 else (question,)
        with self._lock:
            if len(self._split) >= 256:
                self._split.clear()
            self._split[question] = queries
        return queries

    def _shared(self, key: object, work: Callable[[], Any]) -> Any:
        """`work()`, run by the first of those asking for `key` at once; the others wait for
        it and are handed what it returned (or raised). Kept only while it runs: a result
        shared longer would outlive the documents it was found in."""
        with self._lock:
            flight = self._flights.get(key)
            leads = flight is None
            if flight is None:
                flight = self._flights[key] = Future()
        if leads:
            try:
                flight.set_result(work())
            except BaseException as error:
                flight.set_exception(error)
            finally:
                with self._lock:
                    del self._flights[key]
        return flight.result()

    def _ensure_index(self) -> None:
        """The vector index at this model's size: one at another size is dropped and made
        again, since it would hold none of this model's vectors."""
        if self._index_size() not in (None, self._dimensions):
            self._driver.execute_query(f"DROP INDEX `{self._index}`")
        create_vector_index(
            self._driver, self._index, label=CHUNK_LABEL, embedding_property=self._vector,
            dimensions=self._dimensions, similarity_fn="cosine",
        )
        self._driver.execute_query("CALL db.awaitIndex($index)", index=self._index)
        self._rag = None

    def _index_size(self) -> int | None:
        records, _, _ = self._driver.execute_query(
            "SHOW VECTOR INDEXES YIELD name, options WHERE name = $index "
            "RETURN options.indexConfig['vector.dimensions'] AS size",
            index=self._index,
        )
        return int(records[0]["size"]) if records else None

    def _retriever(self) -> _OnceRetriever:
        if self._rag is None:
            if self._index_size() is None:
                raise GraphNotEmbedded(
                    "nothing in the graph space is embedded yet: run `graphme embed-graph`"
                )
            records, _, _ = self._driver.execute_query(
                f"MATCH (c:{CHUNK_LABEL}) WHERE c.`{self._vector}` IS NOT NULL "
                f"AND c.`{self._model_property}` <> $model RETURN c.id LIMIT 1",
                model=self._model,
            )
            if records:
                # Both default embedders are 3072 wide: the index's size cannot tell them apart.
                raise GraphNotEmbedded(
                    "passages were embedded with another model: run `graphme embed-graph`"
                )
            self._rag = _OnceRetriever(
                self._driver,
                floor=self._floor, dense_weight=self._dense_weight, rrf_k=self._rrf_k,
                vector_index_name=self._index,
                fulltext_index_name=self._text_index,
                retrieval_query=self._retrieval_query(),
                embedder=self._embedder,
            )
        return self._rag

    def _retrieval_query(self) -> str:
        vector = f"c.`{self._vector}`"
        # The linked documents nearest the question as a whole, before any passage is read.
        nearest = f"""MATCH (linked:Document {{id: other}})
          WITH other, ways, coalesce(vector.similarity.cosine(
            linked.`{self._centroid}`, $query_vector), 0.0) AS whole
          ORDER BY whole DESC LIMIT {LINK_CANDIDATES}"""
        return f"""
        WITH node AS chunk, score
        MATCH (start:Document {{id: chunk.document_id}})
        CALL (start) {{
          WITH start AS doc
          {_LINKED}
          WITH other, direction WHERE other <> start.id
          WITH other, collect(direction) AS ways
          {nearest}
          RETURN collect({{id: other, direction: {_WAY}, via: start.id, hop: 2}}) AS second
        }}
        CALL (start, second) {{
          UNWIND second AS step
          MATCH (doc:Document {{id: step.id}})
          CALL (doc, step, start, second) {{
            {_LINKED}
            WITH other, direction
            WHERE other <> start.id AND NOT other IN [s IN second | s.id]
            WITH other, collect(direction) AS ways
            {nearest}
            RETURN collect({{id: other, direction: {_WAY}, via: step.id, hop: 3}}) AS found
          }}
          UNWIND found AS link
          RETURN collect(link) AS third
        }}
        CALL (second, third) {{
          UNWIND second + third AS link
          MATCH (reached:Document {{id: link.id}})
          CALL (reached, link) {{
            MATCH (c:{CHUNK_LABEL} {{document_id: reached.id}}) WHERE {vector} IS NOT NULL
            WITH c, vector.similarity.cosine({vector}, $query_vector) AS closeness
            ORDER BY closeness DESC
            RETURN collect({{id: c.id, score: closeness, members: c.members, text: c.text}})[..2]
              AS passages
          }}
          RETURN collect({{document_id: reached.id, file: reached.label, hop: link.hop,
                          via: link.via, direction: link.direction, passages: passages,
                          modified: reached.modified_at}})
            AS linked
        }}
        RETURN chunk.id AS id, score,
               start.id AS document_id, start.label AS file,
               chunk.text AS text, chunk.members AS members, linked,
               start.modified_at AS modified
        """


# DocNode: the label document_id is indexed under; without it every mention in the space is
# read for each document a hop reaches.
_LINKED = """CALL (doc) {
            MATCH (e:DocNode:ExternalDocument {document_id: doc.id})-[:SAME_AS]->(n:Document)
            USING INDEX e:DocNode(document_id)
            RETURN n.id AS other, 'cites' AS direction
            UNION
            MATCH (x:ExternalDocument)-[:SAME_AS]->(doc)
            RETURN x.document_id AS other, 'cited_by' AS direction
            UNION
            MATCH (doc)-[:SIMILAR_TO]-(n:Document)
            RETURN n.id AS other, 'similar' AS direction
          }"""
"""The documents linked to `doc`: those a page of it names, those naming it, and those
related to it by what they say."""

_WAY = ("CASE WHEN 'cites' IN ways THEN 'cites' WHEN 'cited_by' IN ways THEN 'cited_by' "
        "ELSE 'similar' END")
"""A document linked more than one way is reached once, by the way that says the most: a
citation over a likeness."""


_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def _checked(top_k: int) -> None:
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}, not {top_k}")


_PICTURES = f"""
MATCH (n:{NODE_LABEL}) WHERE n.id IN $ids AND (n:Table OR n:PivotTable OR n:Chart OR n:Image)
  AND n[$key] IS NOT NULL
MATCH (d:Document {{id: n.document_id}})
RETURN n.id AS id, n.label AS label, n[$key] AS crop, d.file_name AS file
"""
"""Which of a passage's members are pictures with a stored crop (`VISUAL_TYPES`)."""


def _prompt(question: str, context: str) -> str:
    """The question under its context, and for a Korean or an English question -- or one with
    no language of its own, which is answered in English -- the language said again beside
    it: told only in the system prompt, ahead of a long context, a smaller model answered
    Korean questions in English, and English questions in Korean."""
    prompt = TEMPLATE.format(query_text=question, context=context)
    if _is_korean(question):
        return prompt.replace("\n\nAnswer:", "\n\nAnswer in Korean:")
    if _is_english(question):
        return prompt.replace("\n\nAnswer:", "\n\nAnswer in English:")
    return prompt


def _is_korean(text: str) -> bool:
    return any("\uac00" <= character <= "\ud7a3" for character in text)


def _is_english(text: str) -> bool:
    """English, or no language of its own (a number, a code): English is the default."""
    return all(character.isascii() for character in text if character.isalpha())


def _nothing_found(question: str) -> str:
    """What is said when nothing in the graph is near the question, in its language."""
    if _is_korean(question):
        return "그래프에서 이 질문과 관련된 문서를 찾지 못했습니다."
    return "No document in the graph is related to this question."


def _lucene_escaped(text: str) -> str:
    """The question as plain words for the fulltext index, always: every Lucene special
    character escaped -- a `?` ending a Korean question is otherwise a wildcard, and a wildcard
    term skips the analyzer, so its last word never matched -- and the operators Lucene reads
    only in capitals (AND, OR, NOT) written in small letters. The vector search is given the
    question as it was asked."""
    escaped = "".join(f"\\{ch}" if ch in _LUCENE_SPECIAL else ch for ch in text)
    return re.sub(r"\b(AND|OR|NOT)\b", lambda m: m.group(1).lower(), escaped)


def _item(record: neo4j.Record) -> RetrieverResultItem:
    """A passage as the model reads it -- it opens with its file and the headings above it,
    which is what an answer cites -- then the documents linked to its own, hop by hop, each
    with how it is linked and its passages nearest the question.

    Composed (NFC) throughout: macOS names a Korean file in loose jamo, and a model copying a
    name back that way broke it into letters no document on the page is called."""
    files = {record["document_id"]: _composed(record["file"])}
    linked = [{**doc, "file": _composed(doc["file"])} for doc in record["linked"]]
    files.update({doc["document_id"]: doc["file"] for doc in linked})
    lines = [record["text"]]
    for doc in linked:
        how = _HOW[doc["direction"]]
        lines.append(
            f"Linked document, hop {doc['hop']} ({files.get(doc['via'], doc['via'])} {how} it) "
            f"[{doc['file']}]:"
        )
        shown = doc["passages"][: LINKED_PASSAGES[doc["hop"]]]
        lines += [f"- {passage['text']}" for passage in shown]
    return RetrieverResultItem(
        content=_composed("\n".join(lines)),
        metadata={
            "id": record["id"],
            "score": record["score"],
            "document_id": record["document_id"],
            "file": files[record["document_id"]],
            "members": record["members"],
            "linked": linked,
            "modified": record["modified"],
        },
    )


def _composed(text: str) -> str:
    return unicodedata.normalize("NFC", text)


_HOW = {"cites": "cites", "cited_by": "is cited by", "similar": "is similar to"}
"""How the document a link starts from stands to the one it reaches, as the model reads it."""


def fusion_weights(question: str, *, dense: float) -> tuple[float, float]:
    """How much the vector and the fulltext search count for this question. One naming a
    document or part by its number leans on the words: an identifier is matched by its letters,
    and BM25 serves specific questions better than cosine (arXiv:2504.05324). A weight already
    leaning on the words stays as set."""
    text = 1.0 - dense
    if _names_an_identifier(question) and dense > text:
        return round(text, 6), round(dense, 6)
    return round(dense, 6), round(text, 6)


_IDENTIFIER = re.compile(r"(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[A-Za-z0-9][\w-]{2,}", re.ASCII)
"""A word of Latin letters and digits together, three characters or more -- QP-210, V50-0003,
ZX42 -- the way a document, a part or a lot is named; a plain number or a unit is not one."""


def _names_an_identifier(question: str) -> bool:
    return bool(named_identifiers(question))


def candidates(top_k: int) -> int:
    """Each search's candidates for the fusion: four times top_k, at least 200. A passage only
    one search ranks is still fused, so each list reaches well past top_k -- the ANN search's
    recall at that depth bounds what the fusion can find. The answer is often first in one
    search and far down the other: at 40 it was left with one search's part and lost to
    passages middling in both."""
    return max(200, 4 * top_k)


class _OnceRetriever(HybridCypherRetriever):
    """Neo4j's two indexes -- vector (ANN) and fulltext (BM25) -- searched by our own query
    (`fused`), the rankings fused by weighted reciprocal rank fusion rather than the library's
    max-normalised scores; each document shown once across what a question finds (`_once`),
    and only passages something says are about the question (`_FUSED`). It keeps the library's
    retriever for its constructor, which checks the two indexes are there."""

    def __init__(self, driver: neo4j.Driver, *, floor: float, dense_weight: float, rrf_k: int,
                 **kwargs: Any) -> None:
        super().__init__(driver, **kwargs)
        self.floor = floor
        """`Settings.relevance_floor`."""
        self.dense_weight = dense_weight
        """`Settings.hybrid_dense_weight`."""
        self.rrf_k = rrf_k
        """`Settings.rrf_k`."""

    def fused(self, question: str, top_k: int) -> list[neo4j.Record]:
        """The passages both searches rank, fused: a passage ranked r by a search counts that
        search's weight / (rrf_k + r), and its score is the sum, scaled so a passage first in
        both scores 1 -- the relative relevance the page shows. A passage is fused only with
        evidence it is about the question (`_FUSED`); none, and the search found nothing.
        Highest score first: the query's later steps keep no order."""
        assert self.embedder is not None
        if not self._holds_any(named_identifiers(question)):
            return []
        dense, text = fusion_weights(question, dense=self.dense_weight)
        records, _, _ = self.driver.execute_query(
            _FUSED + self.retrieval_query,
            {
                "vector_index_name": self.vector_index_name,
                "fulltext_index_name": self.fulltext_index_name,
                "query_text": _lucene_escaped(question),
                "every_word": every_word(question), "floor": self.floor,
                "query_vector": self.embedder.embed_query(question),
                "candidates": candidates(top_k),
                "dense_weight": dense, "text_weight": text, "rrf_k": self.rrf_k,
                "top_k": top_k,
            },
            database_=self.neo4j_database,
            routing_=neo4j.RoutingControl.READ,
        )
        return sorted(records, key=lambda record: record["score"], reverse=True)


    def _holds_any(self, named: list[str]) -> bool:
        """Whether some passage holds one of the identifiers a question names -- true when it
        names none. One naming only numbers nothing holds asks about a document not here, and
        passages that merely sound alike are no answer to it."""
        if not named:
            return True
        records, _, _ = self.driver.execute_query(
            "CALL db.index.fulltext.queryNodes($index, $words, {limit: 1}) YIELD node "
            "RETURN count(node) AS found",
            {"index": self.fulltext_index_name,
             "words": any_named(named)},
            database_=self.neo4j_database,
            routing_=neo4j.RoutingControl.READ,
        )
        return bool(records and records[0]["found"])


_FUSED = """
CALL () {
  CALL db.index.vector.queryNodes($vector_index_name, $candidates, $query_vector)
    YIELD node, score
  WITH collect({node: node, near: 2 * score - 1 >= $floor}) AS ranked
  UNWIND range(0, size(ranked) - 1) AS at
  RETURN ranked[at].node AS node, $dense_weight / ($rrf_k + at + 1) AS part,
         ranked[at].near AS about
  UNION ALL
  CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $candidates})
    YIELD node, score
  WITH collect(node) AS ranked
  UNWIND range(0, size(ranked) - 1) AS at
  RETURN ranked[at] AS node, $text_weight / ($rrf_k + at + 1) AS part, false AS about
  UNION ALL
  WITH $every_word AS words WHERE words <> ''
  CALL db.index.fulltext.queryNodes($fulltext_index_name, words, {limit: $candidates})
    YIELD node
  RETURN node, 0.0 AS part, true AS about
}
WITH node, sum(part) * ($rrf_k + 1) AS score, true IN collect(about) AS about
WHERE about
ORDER BY score DESC, elementId(node)
LIMIT $top_k
"""
"""Weighted reciprocal rank fusion of Neo4j's two indexes over the passages, each searched to
`candidates`. Rank says nothing about whether anything was found -- every search has a first --
so a passage is kept only with evidence it is about the question: its cosine (Neo4j gives
(1 + cos) / 2) reaches `relevance_floor`, or it holds the question's words (`every_word`). A
fulltext match on some of them is not that: a nonsense question shares a common word with
something. What follows is the retrieval query, which reads `node` and `score`."""


def every_word(question: str) -> str:
    """A Lucene query for the passages holding every word of the question, each as a phrase so a
    word cut into pieces by the analyzer is still matched whole -- or every identifier it names
    (`_IDENTIFIER`, `document_numbers`), since a question about a numbered document is answered
    by the passages holding the number, whatever else it asks. Empty with no word in it."""
    # Lower case, as the analyzer reads them, so the same word is not asked for twice.
    words = _words(question)
    named = named_identifiers(question)
    groups = [" AND ".join(_held(word) for word in group) for group in (words, named) if group]
    return " OR ".join(f"({group})" for group in dict.fromkeys(groups))


def any_named(named: Sequence[str]) -> str:
    """A Lucene query for the passages holding any of these identifiers (`_held`)."""
    return " OR ".join(_held(word) for word in named)


def _held(word: str) -> str:
    """A word as the index holds it: the phrase -- or, for one ending in Latin letters or
    digits, the same pieces with Hangul run on after the last. The `cjk` analyzer keeps a
    particle on what it follows ("QP-210의" is the token "210의"), so the phrase alone missed
    every Korean sentence naming the number; only Hangul may run on, so 210 is not 2100."""
    pieces = re.findall(r"[^\W_]+", word)
    if not pieces or not re.fullmatch(r"[a-z0-9]+", pieces[-1]):
        return f'"{word}"'
    run_on = " AND ".join([*pieces[:-1], f"/{pieces[-1]}[가-힣]+/"])
    return f'("{word}" OR ({run_on}))'


def _words(question: str) -> list[str]:
    return list(dict.fromkeys(w.lower() for w in re.findall(r"[^\W_][\w-]*", question)))


def named_identifiers(question: str) -> list[str]:
    """The document numbers and identifiers (`_IDENTIFIER`) a question names, lower case and
    without the particle written right after one ("QP-210의"). A word is cut where its Latin
    letters and digits end."""
    words = (word.lower() for word in re.findall(r"[A-Za-z0-9][\w-]*", question, re.ASCII))
    numbers = (re.sub(r"[가-힣]+$", "", number) for number in sorted(document_numbers(question)))
    return list(dict.fromkeys([*numbers, *(w for w in words if _IDENTIFIER.fullmatch(w))]))


def _once(records: list[neo4j.Record], *, each_folder: bool = False) -> list[neo4j.Record]:
    """Each found passage with the documents linked to its own that it brings along: at each
    hop the LINKED_DOCUMENTS whose passages come nearest the question, hop 2 before hop 3, a
    hop-3 document only from a hop-2 one it kept. A document is shown once, at the first hop
    that reaches it -- one a question found (hop 1) is never brought along again, and a linked
    one goes with the best-ranked passage that reaches it.

    An answer takes LINKED_DOCUMENTS at hop 3 in all, so what the model reads stays short;
    `each_folder` -- the Q folder page, where every hop-2 document is a folder -- takes that
    many under each hop-2 document instead."""
    def best(doc: dict[str, Any]) -> float:
        return max((p["score"] for p in doc["passages"]), default=-1.0)

    shown = {record["document_id"] for record in records}
    kept: list[list[dict[str, Any]]] = [[] for _ in records]
    for hop in (2, 3):
        for record, mine in zip(records, kept, strict=True):
            reachable = {doc["document_id"] for doc in mine}
            candidates = sorted(
                (doc for doc in record["linked"]
                 if doc["hop"] == hop and (hop == 2 or doc["via"] in reachable)),
                key=best, reverse=True)
            taken: dict[str | None, int] = {}
            for doc in candidates:
                under = doc["via"] if each_folder and hop == 3 else None
                if taken.get(under, 0) < LINKED_DOCUMENTS and doc["document_id"] not in shown:
                    shown.add(doc["document_id"])
                    mine.append(doc)
                    taken[under] = taken.get(under, 0) + 1
    return [neo4j.Record({**dict(record), "linked": mine})  # type: ignore[no-untyped-call]
            for record, mine in zip(records, kept, strict=True)]


def _source(item: RetrieverResultItem) -> GraphSource:
    found = dict(item.metadata or {})
    linked = [
        LinkedDocument(
            document_id=doc["document_id"], file=doc["file"], hop=doc["hop"], via=doc["via"],
            direction=doc["direction"], modified=doc["modified"],
            passages=[LinkedPassage(**p) for p in doc["passages"][: LINKED_PASSAGES[doc["hop"]]]],
        )
        for doc in found.pop("linked", [])
    ]
    return GraphSource(context=item.content, linked=linked, **found)

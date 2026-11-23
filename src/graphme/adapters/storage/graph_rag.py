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

import contextlib
import logging
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

import httpx
import neo4j
from neo4j_graphrag.indexes import create_vector_index
from neo4j_graphrag.types import RetrieverResultItem

from graphme.adapters.chat import Endpoint, complete, image_part, is_transient
from graphme.adapters.embedding import BatchEmbedder, Remembered
from graphme.adapters.export.neo4j import CHUNK_LABEL, NODE_LABEL
from graphme.adapters.storage.answer_check import Claim as Claim
from graphme.adapters.storage.answer_check import checked as _claims_checked
from graphme.adapters.storage.answer_prompt import (
    FOLLOW_UP_SYSTEM,
    MAX_QUERIES,
    SPLIT_SYSTEM,
    SYSTEM,
    _FollowUp,
    _nothing_found,
    _prompt,
    _Split,
    asked_after,
    asked_with_samples,
    language_samples,
)
from graphme.adapters.storage.answer_prompt import (
    Turn as Turn,  # what `ask` takes a conversation's earlier turns as
)
from graphme.adapters.storage.hybrid_search import (
    _OnceRetriever,
    documents_with_message_ids,
    linked_without_answered_quotes,
    numbers_named,
)
from graphme.adapters.storage.link_ranking import (
    LINKED_DOCUMENTS as LINKED_DOCUMENTS,
)
from graphme.adapters.storage.link_ranking import (
    GraphSource as GraphSource,
)
from graphme.adapters.storage.link_ranking import (
    LinkedDocument as LinkedDocument,
)
from graphme.adapters.storage.link_ranking import LinkedFrom as LinkedFrom
from graphme.adapters.storage.link_ranking import (
    LinkedPassage as LinkedPassage,
)
from graphme.adapters.storage.link_ranking import (
    _closeness_in,
    _marked,
    _named_in,
)
from graphme.adapters.storage.link_ranking import _once as _once
from graphme.adapters.storage.link_ranking import promoted as promoted
from graphme.adapters.storage.link_ranking import relevant_links as relevant_links
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace, checked_name
from graphme.adapters.storage.similar_documents import SimilarDocuments
from graphme.adapters.storage.workspace_scope import DEFAULT_SCOPE, WorkspaceScope
from graphme.adapters.strict_schema import strict_response_schema
from graphme.application.corpus_links import documents_named
from graphme.application.embedded_documents import shown_name
from graphme.application.graph_chunks import CHUNK_RULE, chunks
from graphme.application.passage_scores import IMPORTANCE_RULE, centroid, importance
from graphme.application.versions import Member, by_document, fold_versions
from graphme.application.visual_crops import CROP_ASSET_KEY, read_crop
from graphme.config import LINK_DISCOUNT
from graphme.domain.text_match import estimated_tokens, folded
from graphme.ports.storage import AssetStore

T = TypeVar("T")

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
# How much of a document's opening each of its passages carries: a byline, a source, a date.
OPENING_CHARS = 240
LINK_CANDIDATES = 6
"""Of the documents linked to one, how many are read passage by passage -- those nearest the
question as a whole (their centroids): a document linked to dozens is not read through all
of them, and a few more than LINKED_DOCUMENTS leave room for one whose best passage is
nearer than its whole."""
SAMPLED_DOCUMENTS = 200
"""Documents a passage each is taken from to see which languages the space is written in."""
ANSWER_PICTURES = 16
"""Most crops one answer is sent: about two a passage it reads. A passage of a slide
full of icons would otherwise send each of them."""
PICTURES_FOLLOW = (
    "\n\nThe tables, pictures and charts the context describes follow, each named by the file "
    "it is in. Read them where the description leaves something out, and cite their file."
)
MAX_DOCUMENTS = 20
"""Most documents a question may ask to find."""
PASSAGES_PER_DOCUMENT = 5
"""Most passages one found document brings: a document holding the answer in several sections
gives all of them, and one taking the whole ranking still leaves room for the others."""
ANSWER_TOKENS = 12_000
"""What the passages an answer is written from may cost, linked documents included: the page
shows every document found, the model reads the best of them."""


TURN_SEARCHES = 64
"""The most searches a turn keeps: a question asks a few, one a query and workspace."""

_TURN: ContextVar[Remembered[Any] | None] = ContextVar("graphme_turn", default=None)
"""The searches made in the turn under way (`one_search`), by reader and key."""


@contextlib.contextmanager
def one_search() -> Iterator[None]:
    """A turn whose stages each search: inside it, every search a reader makes is made once and
    kept until it ends, so an answer started after its search finished still reads the passages
    the search showed. A thread the turn starts takes it along with `contextvars.copy_context`.
    Inside a turn already, it is that turn."""
    if _TURN.get() is not None:
        yield
        return
    token = _TURN.set(Remembered(TURN_SEARCHES))
    try:
        yield
    finally:
        _TURN.reset(token)


def collected(ranked: Sequence[Any], documents: int) -> list[Any]:
    """The passages a question finds, `ranked` best first: walked down until `documents`
    documents are reached, the passage reaching the last one included, each document giving
    at most PASSAGES_PER_DOCUMENT. Asking for fewer documents takes the front of the same walk."""
    taken: list[Any] = []
    given: dict[str, int] = {}
    for passage in ranked:
        doc = passage["document_id"]
        if given.get(doc, 0) == PASSAGES_PER_DOCUMENT:
            continue
        given[doc] = given.get(doc, 0) + 1
        taken.append(passage)
        if len(given) == documents and given[doc] == 1:
            break  # the passage that reached the last document is the walk's end
    return taken


def within(ranked: Sequence[Any], only: frozenset[str] | None) -> list[Any]:
    """The passages of `ranked` in the documents `only` names, in their order; every passage
    when `only` is None. A scope naming nothing in the graph leaves none."""
    return list(ranked) if only is None else [p for p in ranked if p["document_id"] in only]


def linked_without(linked: Sequence[dict[str, Any]],
                   exclude: frozenset[str]) -> list[dict[str, Any]]:
    """The linked documents but those `exclude` names, and the hop-3 ones reached through
    them."""
    return [d for d in linked if d["document_id"] not in exclude and d.get("via") not in exclude]


def linked_within(linked: Sequence[dict[str, Any]], only: frozenset[str] | None
                  ) -> list[dict[str, Any]]:
    """The linked documents inside the scope: a question about chosen documents brings none
    from outside them, nor a hop-3 one reached through a document outside."""
    return list(linked) if only is None else [
        d for d in linked
        if d["document_id"] in only and (d["hop"] < 3 or d["via"] in only)]


def across(found: Sequence[tuple[str, Sequence[T]]], *, rrf_k: int) -> list[tuple[str, T]]:
    """Several workspaces' ranked lists as one, each named by its workspace: fused by
    reciprocal rank, each workspace weighted the same -- a score comes from its workspace's own
    indexes and cannot be compared with another's. A tie goes to the workspace given first."""
    scored = [(1 / (rrf_k + rank + 1), workspace, one)
              for workspace, ranked in found for rank, one in enumerate(ranked)]
    # sorted is stable: equal scores keep the order the workspaces were given in
    return [(workspace, one) for _, workspace, one in sorted(scored, key=lambda s: -s[0])]


def find_across(found: Sequence[tuple[str, Sequence[GraphSource]]], documents: int, *,
                rrf_k: int) -> list[GraphSource]:
    """What each workspace's `find` found, fused (`across`) and walked to `documents`
    documents as one search is (`collected`). Ids come from a file's content, so a document is
    told apart by its workspace too."""
    fused = across(found, rrf_k=rrf_k)
    # The walk counts found documents alone; a linked one ranked among them (`promoted`) goes
    # with the document it was reached from, so the walk shows what each workspace's did.
    ranked = [{"id": (ws, s.id), "document_id": (ws, s.document_id), "source": s}
              for ws, s in fused if s.promoted_from is None]
    kept = {id(p["source"]) for p in collected(ranked, documents)}
    while True:
        shown = {(ws, doc) for ws, s in fused if id(s) in kept
                 for doc in (s.document_id, *(d.document_id for d in s.linked))}
        brought = {id(s) for ws, s in fused if id(s) not in kept and s.promoted_from
                   and (ws, s.promoted_from.via) in shown}
        if not brought:
            break
        kept |= brought
    return [replace(s, workspace=ws) for ws, s in fused if id(s) in kept]


def ask_across(read: Sequence[tuple[str, GraphRag, Sequence[RetrieverResultItem]]],
               question: str, documents: int, *, rrf_k: int,
               history: Sequence[Turn] = (),
               shown: Callable[[GraphAnswer], None] | None = None) -> GraphAnswer:
    """An answer from what each workspace's `read` gave, fused (`across`) and walked as one
    question's passages are (`collected`, `filled`, `within_budget`): each workspace's pictures
    read from its own scope, the answer written as one workspace's is (`written`)."""
    ranked = [{"id": (ws, m["id"]), "document_id": (ws, m["document_id"]), "ws": ws,
               "item": RetrieverResultItem(content=item.content, metadata={**m, "workspace": ws})}
              for ws, item in across([(ws, items) for ws, _, items in read], rrf_k=rrf_k)
              for m in [dict(item.metadata or {})]]
    walked = filled(ranked, collected(ranked, documents))
    kept = walked[: len(within_budget([p["item"] for p in walked]))]
    items = [p["item"] for p in kept]
    # each workspace's pictures read from its own scope, then taken in the fused order
    held: list[list[tuple[str, bytes]]] = [[] for _ in kept]
    for ws, rag, _ in read:
        if mine := [at for at, p in enumerate(kept) if p["ws"] == ws]:
            for at, theirs in zip(mine, rag.pictures([kept[at]["item"] for at in mine]),
                                  strict=True):
                held[at] = theirs
    pictures = [picture for each in held for picture in each][:ANSWER_PICTURES]
    return read[0][1].written(question, items, pictures, history, shown=shown)


def filled(ranked: Sequence[Any], taken: Sequence[Any]) -> list[Any]:
    """What an answer reads: the walk's passages (`collected`), then on down `ranked` in the
    documents the walk reached, best first, for `within_budget` to cut. A document's share keeps
    one from crowding the others out of the walk; once every document asked for has its place,
    the budget left is the found documents' next passages -- a long report holding the answer
    in more sections than its share used to be read from its first five and called silent on
    the rest. A document the walk did not reach is never added here."""
    seen = {passage["id"] for passage in taken}
    reached = {passage["document_id"] for passage in taken}
    return [*taken, *(passage for passage in ranked
                      if passage["document_id"] in reached and passage["id"] not in seen)]


def named_only(ranked: Sequence[Any], named: set[str]) -> list[Any]:
    """What a question naming documents is answered from: those documents' passages alone, best
    first, with no share per document -- `within_budget` takes as many as fit. The others the
    search found answer what the named ones do not; they stand on the page, not in the answer.
    A question naming none is `ranked` as it is."""
    if not named:
        return list(ranked)
    return [passage for passage in ranked if passage["document_id"] in named]


def named_first(ranked: Sequence[Any], named: set[str]) -> list[Any]:
    """`ranked` with the passages of the documents the question names first, each part in the
    order it was ranked: a question that names a document asks about that one before the
    others that mention it or share its words. Only what the search found is moved."""
    return ([passage for passage in ranked if passage["document_id"] in named]
            + [passage for passage in ranked if passage["document_id"] not in named])


def named_phrases(question: str, said: Sequence[str]) -> tuple[str, ...]:
    """What in a question names a document: the phrases the model said do, and every
    document number written in it (`document_numbers`), which needs no model to see -- the
    particle after one ("SOP-2026-0412의") left off."""
    return tuple(dict.fromkeys(
        phrase for phrase in (*said, *numbers_named(question)) if phrase.strip()))


def within_budget(items: Sequence[RetrieverResultItem], budget: int = ANSWER_TOKENS
                  ) -> list[RetrieverResultItem]:
    """The front of `items` the answer reads: as many as fit in `budget` tokens, in order --
    the first always, however long."""
    read: list[RetrieverResultItem] = []
    spent = 0
    for item in items:
        spent += estimated_tokens(str(item.content))
        if read and spent > budget:
            break
        read.append(item)
    return read

ASK_RETRY_DELAYS = (2.0, 5.0)
"""Seconds to wait before asking again when the endpoint turned a question away for now
(`TRANSIENT_STATUS`, a timeout, a dropped connection): someone is waiting on the answer, so
this is short, unlike a page reading's schedule."""


class GraphNotEmbedded(RuntimeError):
    """A question was asked of a space nothing was embedded in yet."""


@dataclass(frozen=True, slots=True)
class GraphAnswer:
    text: str
    sources: list[GraphSource]
    claims: list[Claim] = field(default_factory=list)
    checked: bool = False
    """Whether the claims were checked; false when the check was off or failed."""


class GraphRag:
    _scope: WorkspaceScope = DEFAULT_SCOPE
    """The workspace questions are asked of: its labels in every pattern, its own indexes."""

    def __init__(
        self,
        space: Neo4jGraphSpace,
        *,
        embedder: BatchEmbedder,
        endpoint: Endpoint,
        client: httpx.Client,
        model: str,
        dimensions: int,
        index: str | None = None,
        vector_property: str = VECTOR_PROPERTY,
        relevance_floor: float,
        dense_weight: float,
        rrf_k: int,
        link_discount: float = LINK_DISCOUNT,
        assets: AssetStore | None = None,
        wait: Callable[[float], None] = time.sleep,
        scope: WorkspaceScope = DEFAULT_SCOPE,
        check_answers: bool = False,  # a check is a model call: only a caller that asks gets one
    ) -> None:
        self._space = space
        self._check_answers = check_answers
        self._scope = scope
        self._wait = wait
        self._assets = assets
        """Where the crops a passage's pictures name are read from; none, and an answer is
        given the passages' words alone."""
        self._samples: list[str] | None = None
        """A line of each language the space is written in (`language_samples`), worked out
        on the first question after the passages last changed."""
        self._lock = threading.Lock()
        self._flights: Remembered[Any] = Remembered(0)
        """What is being worked out right now, by what it is for (`_shared`)."""
        self._embedder = embedder
        self._endpoint = endpoint
        self._client = client
        self._model = model
        self._dimensions = dimensions
        # Another index and property are a run of its own beside the space's (the tests).
        self._index = checked_name(index or scope.vector_index)
        self._text_index = scope.text_index
        self._floor = relevance_floor
        self._dense_weight = dense_weight
        self._rrf_k = rrf_k
        self._link_discount = link_discount
        self._vector = checked_name(vector_property)
        self._model_property = checked_name(f"{vector_property}_model")
        self._centroid = checked_name(f"{vector_property}_centroid")
        """On each document: the mean of its passages' vectors, what it is mostly about."""
        self._similar = SimilarDocuments(space.driver, index=self._index,
                                         vector_property=vector_property, scope=scope)
        self._rag: _OnceRetriever | None = None
        """Made on the first question: building a retriever asks the database twice."""
        self.text_only = False
        """Searched by the passages' words alone, the question not embedded: a workspace read
        beside the open one, whose passages another search model embedded."""

    @property
    def _driver(self) -> neo4j.Driver:
        return self._space.driver

    def _query(self, query: str, **parameters: Any) -> Any:
        """`execute_query`, held to the workspace (`WorkspaceScope.cypher`)."""
        return self._driver.execute_query(self._scope.cypher(query), **parameters)

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
        # The space has changed: its languages are looked at again, and a split is kept by
        # the samples it was asked beside (`_asked`), so new ones ask again.
        with self._lock:
            self._samples = None
        self._ensure_index()
        records, _, _ = self._query(
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
            self._query(
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
        self._space.group_versions()
        return len(rows)

    def _rate(self, document_ids: Sequence[str] | None) -> None:
        """Each passage's importance in its document (`importance`) and each document's
        centroid, for every document with a passage not rated by this rule -- embedding a
        passage takes its rating away. Read and written about RATE_PASSAGES passages at a
        time: a space's vectors are more than a machine holds."""
        records, _, _ = self._query(
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
        records, _, _ = self._query(
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
                self._scope.cypher(f"""
                UNWIND $rows AS row
                MATCH (c:{CHUNK_LABEL} {{id: row.id}})
                SET c.importance = row.importance, c.importance_rule = $rule
                """),
                rows=rated, rule=IMPORTANCE_RULE,
            )
            tx.run(
                self._scope.cypher("""
                UNWIND $rows AS row
                MATCH (d:Document {id: row.document})
                CALL db.create.setNodeVectorProperty(d, $property, row.centroid)
                REMOVE d.similarity_rule
                """),
                rows=centres, property=self._centroid,
            )

        with self._driver.session() as session:
            session.execute_write(write)

    def ask(self, question: str, *, documents: int, history: Sequence[Turn] = (),
            only: frozenset[str] | None = None, exclude: frozenset[str] = frozenset(),
            shown: Callable[[GraphAnswer], None] | None = None) -> GraphAnswer:
        """The question answered, from the documents `only` names when it is given;
        after earlier `history` turns, searched as the question it stands for
        (`_asked`) and answered with those turns above its passages. `shown` is handed the
        answer before its claims are checked (`written`)."""
        _checked(documents)
        items = within_budget(self.read(question, documents=documents, history=history,
                                     only=only, exclude=exclude))
        # A graph_chunk holding a table, a picture or a chart says it in words a model wrote
        # once; the picture goes too, so the answer reads it, not the description.
        pictures = [picture for each in self.pictures(items) for picture in each]
        return self.written(question, items, pictures, history, shown=shown)

    def written(self, question: str, items: Sequence[RetrieverResultItem],
                pictures: Sequence[tuple[str, bytes]],
                history: Sequence[Turn] = (), *,
                shown: Callable[[GraphAnswer], None] | None = None) -> GraphAnswer:
        """The answer (`answered`) with its sources, and its claims checked when that is on
        and there is a passage to check them against. `shown` is handed the answer before the
        check, so a caller can say it while the check runs; what it raises stops the check."""
        text = self.answered(question, items, pictures, history)
        sources = promoted([_source(item) for item in items], self._link_discount,
                           _named_in(items), _closeness_in(items))
        answer = GraphAnswer(text=text, sources=sources)
        if shown is not None:
            shown(answer)
        claims = self.checked(text, items) if self._check_answers and items else None
        return replace(answer, claims=claims or [], checked=claims is not None)

    def checked(self, answer: str, items: Sequence[RetrieverResultItem]) -> list[Claim] | None:
        """What each claim of `answer` comes to against the passages carrying the files it
        cites (`answer_check.checked`); None when it cites none or the check could not be
        made -- the answer stands."""
        return _claims_checked(answer, items, self._complete)

    def read(self, question: str, *, documents: int, history: Sequence[Turn] = (),
             only: frozenset[str] | None = None,
             exclude: frozenset[str] = frozenset()) -> list[RetrieverResultItem]:
        """What an answer reads, best first, before `within_budget` cuts it."""
        family = self._families()
        found = self._found(question, documents, read_on=True, history=history,
                            only=only, exclude=exclude, family=family)
        return [_item(record) for record in _once(found, family=family)]

    def answered(self, question: str, items: Sequence[RetrieverResultItem],
                 pictures: Sequence[tuple[str, bytes]], history: Sequence[Turn] = ()) -> str:
        """The answer written from `items` and the pictures they hold."""
        if not items:
            # Nothing near the question: no passage to answer from, and no model call for it.
            return _nothing_found(question)
        prompt = _prompt(question, "\n".join(item.content for item in items), history)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt + (PICTURES_FOLLOW if pictures else "")}]
        for label, png in pictures:
            content += [{"type": "text", "text": label}, image_part(png)]
        return self._complete(system=SYSTEM, content=content)

    def pictures(self, items: Sequence[RetrieverResultItem]) -> list[list[tuple[str, bytes]]]:
        """The crops of the tables, pictures and charts the found passages hold, and the scans
        their words were read off, each under the first passage holding it, in the order the
        passages were found, each named by its file and label -- at most ANSWER_PICTURES of
        them in all."""
        pictures: list[list[tuple[str, bytes]]] = [[] for _ in items]
        if self._assets is None or not items:
            return pictures
        first: dict[str, int] = {}
        for at, item in enumerate(items):
            for member in (item.metadata or {}).get("members", []):
                first.setdefault(member, at)
        records, _, _ = self._query(
            _PICTURES, ids=list(first), key=f"meta_{CROP_ASSET_KEY}",
            routing_=neo4j.RoutingControl.READ,
        )
        by_id = {record["id"]: record for record in records}
        sent: set[str] = set()
        taken = 0
        for member, at in first.items():
            record = by_id.get(member)
            if record is None or record["picture"] in sent:
                continue
            sent.add(record["picture"])
            # A crop not kept is a picture the passage's words still describe.
            if (png := read_crop(record["crop"], self._assets)) is None:
                continue
            named = shown_name(_composed(record["file"]))
            pictures[at].append((f"Picture in [{named}]: {record['label']}", png))
            taken += 1
            if taken == ANSWER_PICTURES:
                break
        return pictures

    def find(self, question: str, *, documents: int, history: Sequence[Turn] = (),
             only: frozenset[str] | None = None,
             exclude: frozenset[str] = frozenset()) -> list[GraphSource]:
        """The passages a question finds and the documents linked to theirs, with no answer
        written -- what the Q folder page shows: as `ask` reads them, but with
        LINKED_DOCUMENTS hop-3 documents under each hop-2 one rather than in all."""
        _checked(documents)
        family = self._families()
        found = self._found(question, documents, history=history, only=only, exclude=exclude,
                            family=family)
        items = [_item(record) for record in _once(found, each_folder=True, family=family)]
        return promoted([_source(item) for item in items], self._link_discount,
                        _named_in(items), _closeness_in(items))

    def _found(self, question: str, documents: int, *, read_on: bool = False,
               history: Sequence[Turn] = (),
               only: frozenset[str] | None = None,
               exclude: frozenset[str] = frozenset(),
               family: Mapping[str, Sequence[Member]] | None = None) -> list[neo4j.Record]:
        """The fused search for each thing the question asks (`_queries`), the passages taken
        from each search in turn, best first, each once -- walked until `documents` documents
        are reached (`collected`) -- then the documents linked to each one found, walked once
        a document. A document the question names, where the search found it, comes first
        (`named_first`). An answer and its folder page ask side by side: the search runs once
        for both, and asking for fewer documents takes the front of the same passages.
        `only` and `exclude` name documents as they were when chosen: each is read as the one
        the graph holds for it now (`current`), so an edited file's new reading answers.
        `only` searches the passages of the documents it names, and brings the linked
        documents inside it alone; a scoped search is shared by its scope.
        `only` empty finds nothing, None is every document. `exclude` leaves its documents, and
        their copies and versions, out of this question alone: their passages are not found
        nor are they brought as linked.
        Copies and versions of one document (`family`, `_families`) fold to the best-ranked of
        them (`fold_versions`) once `only` and `exclude` are applied."""
        queries, said, searched = self._asked(question, history)
        only, exclude = self._resolved_scope(only, exclude, family or {})

        def search() -> list[neo4j.Record]:
            retriever = self._retriever()
            searches = [retriever.fused(query, only) for query in queries]
            found: dict[str, neo4j.Record] = {}
            for rank in range(max(map(len, searches), default=0)):
                for one in searches:
                    if rank < len(one):
                        found.setdefault(one[rank]["id"], one[rank])
            return list(found.values())

        # kept by its queries: the same words after other turns are another search
        key = ("found", queries) if only is None else ("found", queries, only)
        ranked = [record for record in within(cast(list[neo4j.Record], self._shared(key, search)),
                                              only)
                  if record["document_id"] not in exclude]
        every = self._named(named_phrases(searched, said))
        named = every & {record["document_id"] for record in ranked}
        ranked = fold_versions(named_first(ranked, named), family or {}, only)
        named &= {record["document_id"] for record in ranked}
        order = named_first(ranked, named)
        taken = collected(order, documents)
        if not read_on:  # the page shows the walk
            return _marked(self._linked(taken, searched, only, exclude), every)
        # an answer reads only in the documents the question names (`named_only`), else on
        # into the documents found (`filled`)
        return _marked(self._linked(named_only(ranked, named) if named else filled(order, taken),
                                    searched, only, exclude), every)

    def _resolved_scope(self, only: frozenset[str] | None, exclude: frozenset[str],
                        family: Mapping[str, Sequence[Member]]
                        ) -> tuple[frozenset[str] | None, frozenset[str]]:
        """`only` and `exclude` as the graph holds each document now (`current`), and
        `exclude` with the copies and versions of what it leaves out -- save one `only` names."""
        if asked := (only or frozenset()) | exclude:
            now = self.current(asked)
            only = None if only is None else frozenset(now[i] for i in only if i in now)
            exclude = frozenset(now[i] for i in exclude if i in now)
        return only, exclude | {m.document_id for d in exclude for m in family.get(d, ())
                                if only is None or m.document_id not in only}

    def current(self, ids: frozenset[str]) -> dict[str, str]:
        """Each of `ids` to the document the graph holds for it now: itself, or the one read
        from its file since it was edited; one deleted since is left out
        (`Neo4jGraphSpace.current`). Looked up once a turn, however many of its stages ask."""
        return cast(dict[str, str], self._shared(
            ("current", ids), lambda: self._space.current(sorted(ids))))

    def _families(self) -> dict[str, tuple[Member, ...]]:
        """Each document of a family of copies and versions, by id, to its whole family -- read
        once a turn (`_shared`), so an ungrouped document stands alone from the next."""
        return cast(dict[str, tuple[Member, ...]], self._shared(
            ("families",), lambda: by_document(self._space.version_pairs())))

    def _named(self, phrases: Sequence[str]) -> set[str]:
        """The documents `phrases` name, found or not."""
        if not phrases:
            return set()
        # ponytail: the space's identities read once a turn naming something; keep them with
        # the graph's fingerprint if a space of thousands makes this slow.
        return documents_named(phrases, self._shared(("identities",), self._space.identities))

    def _linked(self, found: list[neo4j.Record], question: str,
                only: frozenset[str] | None = None,
                exclude: frozenset[str] = frozenset()) -> list[neo4j.Record]:
        """`found` with the documents linked to each document in it, hop by hop, given to the
        document's best passage: the walk is the document's, so it is made once a document,
        however many of its passages were found."""
        first: dict[str, int] = {}
        for at, record in enumerate(found):
            first.setdefault(record["document_id"], at)
        if not first:
            return found
        retriever = self._retriever()
        if retriever.text_only:  # the links are ranked by the question's vector
            return [neo4j.Record({**dict(record), "linked": []})  # type: ignore[no-untyped-call]
                    for record in found]
        assert retriever.embedder is not None
        records, _, _ = self._query(
            self._links_query(), ids=list(first),
            passages=[record["id"] for record in found],
            query_vector=retriever.embedder.embed_query(question),
            database_=retriever.neo4j_database,
            routing_=neo4j.RoutingControl.READ,
        )
        quoted = {p["quote_of"] for record in records for doc in record["linked"]
                  for p in doc["passages"] if p.get("quote_of")}
        held = documents_with_message_ids(self._driver, sorted(quoted),
                                          database=retriever.neo4j_database, scope=self._scope)
        links = {record["document_id"]: linked_without_answered_quotes(
                     linked_without(linked_within(
                         relevant_links(record["linked"], self._floor), only), exclude),
                     in_space=held)
                 for record in records}
        near = {p["id"]: p["closeness"] for p in (records[0].get("near") or [] if records else [])}
        return [
            neo4j.Record({**dict(record),  # type: ignore[no-untyped-call]
                          "closeness": near.get(record["id"]),
                          "linked": links.get(record["document_id"], [])
                          if first[record["document_id"]] == at else []})
            for at, record in enumerate(found)
        ]

    def _asked(self, question: str, history: Sequence[Turn] = ()) -> _Asked:
        """What the question asks, one search query each, the phrases in it naming a
        document (`SPLIT_SYSTEM`), and the question searched for: after earlier turns, the
        question as it reads on its own (`FOLLOW_UP_SYSTEM`) -- or the question itself, as
        one query naming nothing, where the model asks it as one thing or gives no usable
        answer. Made once for a question, the turns before it and the samples it is asked
        beside, however many workspaces ask it, at once or after (`SPLITS`); a split that
        failed is not kept."""
        key, asked = self._split_key(question, history)
        split = SPLITS.get(key, lambda: self._split_question(question, asked, history),
                           keep=lambda split: split is not None)
        return split or ((question,), (), question)

    def last_queries(self, question: str, history: Sequence[Turn] = ()) -> tuple[str, ...]:
        """What the question was searched for, read from the split kept when it was asked
        (`_asked`) -- no model is asked: the question itself when none is kept."""
        key, _ = self._split_key(question, history)
        split = SPLITS.get(key, lambda: None, keep=lambda split: split is not None)
        return split[0] if split else (question,)

    def _split_key(self, question: str, history: Sequence[Turn]) -> tuple[tuple[str, ...], str]:
        """What a split is kept by -- the endpoint and model asked and what they are sent --
        and what they are sent."""
        asked = asked_after(history, asked_with_samples(question, self._language_samples()))
        return (self._endpoint.wire, self._endpoint.base_url, self._endpoint.model, asked), asked

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

    def _split_question(self, question: str, asked: str,
                        history: Sequence[Turn] = ()) -> _Asked | None:
        # A question with no earlier turns is split by SPLIT_SYSTEM alone.
        system, shape = (FOLLOW_UP_SYSTEM, _FollowUp) if history else (SPLIT_SYSTEM, _Split)
        try:
            reply = self._complete(
                system=system, content=asked,
                schema=strict_response_schema(shape), name="question_split")
            written = shape.model_validate_json(reply)
        except Exception as error:  # noqa: BLE001 -- a question unsplit is asked as written
            _log.warning("question not split -- %s", str(error)[:200])
            return None  # not kept: the next ask tries the model again
        # The question as it reads on its own is what is searched for and what names a document.
        standalone = written.standalone if isinstance(written, _FollowUp) else ""
        searched = standalone.strip() or question
        parts = _kept(written.queries, MAX_QUERIES)
        # The same queries in the documents' other languages: what full-text search matches.
        queries = tuple(dict.fromkeys((*(parts if len(parts) > 1 else (searched,)),
                                       *_kept(written.translated, MAX_QUERIES * 2))))
        # A name is one the question writes: one copied off the samples names nothing asked.
        held = folded(f"{question}\n{searched}")
        named = [phrase for phrase in written.documents if folded(phrase) in held]
        return queries, _kept(named, MAX_QUERIES), searched

    def _language_samples(self) -> list[str]:
        """A line of each language the space's documents are written in, from one passage of
        each of up to SAMPLED_DOCUMENTS documents -- none when the space cannot say: the
        question is split all the same."""
        with self._lock:
            if self._samples is not None:
                return self._samples
        try:
            records, _, _ = self._query(
                f"""
            MATCH (d:Document) WITH d LIMIT $documents
            CALL {{
                WITH d
                MATCH (c:{CHUNK_LABEL} {{document_id: d.id}})
                RETURN c.text AS text LIMIT 1
            }}
            RETURN text
            """,
                documents=SAMPLED_DOCUMENTS, routing_=neo4j.RoutingControl.READ,
            )
        except Exception as error:  # noqa: BLE001 -- the split goes on without them
            _log.warning("languages of the space not read -- %s", str(error)[:200])
            return []  # not kept: the next question looks again
        samples = language_samples([r["text"] for r in records if r["text"]])
        with self._lock:
            self._samples = samples
        return samples

    def _shared(self, key: object, work: Callable[[], Any]) -> Any:
        """`work()`, run by the first of those asking for `key` at once; the others wait for
        it and are handed what it returned (or raised). Kept only while it runs, or inside a
        turn (`one_search`) until the turn ends: a result shared longer would outlive the
        documents it was found in."""
        turn = _TURN.get()
        if turn is None:
            return self._flights.get(key, work)
        return turn.get((self, key), lambda: self._flights.get(key, work))

    def _ensure_index(self) -> None:
        """The vector index at this model's size: one at another size is dropped and made
        again, since it would hold none of this model's vectors."""
        if self._index_size() not in (None, self._dimensions):
            self._query(f"DROP INDEX `{self._index}`")
        create_vector_index(
            self._driver, self._index, label=self._scope.chunk_label,
            embedding_property=self._vector,
            dimensions=self._dimensions, similarity_fn="cosine",
        )
        self._query("CALL db.awaitIndex($index)", index=self._index)
        self._rag = None

    def _index_size(self) -> int | None:
        records, _, _ = self._query(
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
            records, _, _ = self._query(
                f"MATCH (c:{CHUNK_LABEL}) WHERE c.`{self._vector}` IS NOT NULL "
                f"AND c.`{self._model_property}` <> $model RETURN c.id LIMIT 1",
                model=self._model,
            )
            if records and not self.text_only:
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
                embedder=None if self.text_only else self._embedder,
                text_only=self.text_only,
                scope=self._scope,
            )
        return self._rag

    def _retrieval_query(self) -> str:
        """A passage found, as the search hands it on: what it says and whose it is."""
        return f"""
        WITH node AS chunk, score
        MATCH (start:Document {{id: chunk.document_id}})
        OPTIONAL MATCH (first:{CHUNK_LABEL} {{id: start.id + ':0'}})
        RETURN chunk.id AS id, score,
               start.id AS document_id, start.label AS file,
               chunk.text AS text, chunk.members AS members, chunk.quote_of AS quote_of,
               start.modified_at AS modified, first.text AS opening,
               start.meta_mail_from AS mail_from, start.meta_mail_date AS mail_date,
               start.meta_mail_subject AS mail_subject,
               start.meta_mail_message_id AS mail_message_id,
               COLLECT {{ MATCH (x:ExternalDocument)-[:SAME_AS]->(start)
                          MATCH (m:Document {{id: x.document_id}})
                          WHERE m.meta_mail_message_id IS NOT NULL
                          WITH DISTINCT m ORDER BY m.meta_mail_sent_at DESC LIMIT {MAIL_CARRIERS}
                          RETURN {{sender: m.meta_mail_from, date: m.meta_mail_date,
                                  subject: m.meta_mail_subject}} }} AS carriers,
               COLLECT {{ MATCH (chunk)-[:COVERS]->(u) WHERE u.meta_page IS NOT NULL
                          RETURN DISTINCT u.meta_page ORDER BY u.meta_page }} AS pages
        """

    def _links_query(self) -> str:
        """The documents linked to each of `$ids`, hop by hop, each with its passages nearest
        the question (`$query_vector`), and how near the question each found passage of
        `$passages` is, on the same measure (`promoted`)."""
        vector = f"c.`{self._vector}`"
        linked = _links_of(self._scope)
        # The linked documents nearest the question as a whole, before any passage is read.
        nearest = f"""MATCH (linked:Document {{id: other}})
          WITH other, ways, coalesce(vector.similarity.cosine(
            linked.`{self._centroid}`, $query_vector), 0.0) AS whole
          ORDER BY whole DESC LIMIT {LINK_CANDIDATES}"""
        return f"""
        CALL () {{
          UNWIND $passages AS passage
          MATCH (c:{CHUNK_LABEL} {{id: passage}}) WHERE {vector} IS NOT NULL
          RETURN collect({{id: c.id, closeness: vector.similarity.cosine({vector}, $query_vector)}})
            AS near
        }}
        UNWIND $ids AS found
        MATCH (start:Document {{id: found}})
        CALL (start) {{
          WITH start AS doc
          {linked}
          WITH other, direction WHERE other <> start.id
          WITH other, collect(direction) AS ways
          {nearest}
          RETURN collect({{id: other, direction: {_WAY}, via: start.id, hop: 2}}) AS second
        }}
        CALL (start, second) {{
          UNWIND second AS step
          MATCH (doc:Document {{id: step.id}})
          CALL (doc, step, start, second) {{
            {linked}
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
            RETURN collect({{id: c.id, score: closeness, members: c.members, text: c.text,
                            quote_of: c.quote_of}})[..2]
              AS passages
          }}
          RETURN collect({{document_id: reached.id, file: reached.label, hop: link.hop,
                          via: link.via, direction: link.direction, passages: passages,
                          modified: reached.modified_at}})
            AS linked
        }}
        RETURN start.id AS document_id, linked, near
        """


def _links_of(scope: WorkspaceScope) -> str:
    """The documents linked to `doc`: those a page of it names, those naming it, and those
    related to it by what they say."""
    # The workspace's label is what document_id is indexed under; without it every mention in
    # the space is read for each document a hop reaches.
    return f"""CALL (doc) {{
            MATCH (e:DocNode:ExternalDocument {{document_id: doc.id}})-[:SAME_AS]->(n:Document)
            USING INDEX e:{scope.node_label}(document_id)
            RETURN n.id AS other, 'cites' AS direction
            UNION
            MATCH (x:ExternalDocument)-[:SAME_AS]->(doc)
            RETURN x.document_id AS other, 'cited_by' AS direction
            UNION
            MATCH (doc)-[:SIMILAR_TO]-(n:Document)
            RETURN n.id AS other, 'similar' AS direction
          }}"""

_Asked = tuple[tuple[str, ...], tuple[str, ...], str]
"""A question's split: its search queries, the phrases naming a document, and the question as
it is searched for."""
SPLITS: Remembered[_Asked | None] = Remembered(256)
"""Each question's split (`GraphRag._asked`), by the endpoint and model asked and what they
were sent -- the question beside its workspace's language samples, after the turns before
it: workspaces written alike ask once, one written in another language asks for its own
translations, and the same words after other turns are another question."""

_WAY = ("CASE WHEN 'cites' IN ways THEN 'cites' WHEN 'cited_by' IN ways THEN 'cited_by' "
        "ELSE 'similar' END")
"""A document linked more than one way is reached once, by the way that says the most: a
citation over a likeness."""


def _kept(written: Sequence[str], most: int) -> tuple[str, ...]:
    """What the model wrote, trimmed, once each, at most `most` of it."""
    return tuple(dict.fromkeys(part for item in written if (part := item.strip())))[:most]


def _checked(documents: int) -> None:
    if not 1 <= documents <= MAX_DOCUMENTS:
        raise ValueError(f"documents must be between 1 and {MAX_DOCUMENTS}, not {documents}")


_PICTURES = f"""
MATCH (m:{NODE_LABEL}) WHERE m.id IN $ids
OPTIONAL MATCH (s:{NODE_LABEL} {{id: m.meta_scan_of}})
OPTIONAL MATCH (f:{NODE_LABEL} {{id: m.meta_part_of_figure}})
WITH m, CASE WHEN f IS NOT NULL AND f[$key] IS NOT NULL THEN f
             WHEN (m:Table OR m:PivotTable OR m:Chart OR m:Image) AND m[$key] IS NOT NULL
                  AND m.meta_background IS NULL
             THEN m ELSE s END AS n
WHERE n IS NOT NULL AND (n:Table OR n:PivotTable OR n:Chart OR n:Image) AND n[$key] IS NOT NULL
MATCH (d:Document {{id: n.document_id}})
RETURN m.id AS id, n.id AS picture, n.label AS label, n[$key] AS crop, d.file_name AS file
"""
"""Which of a passage's members are pictures with a stored crop (`VISUAL_TYPES`) -- a table
or a chart read off a scan by its own crop -- and for words read off a scan, the scan they were
read from. A scan is sent as what words were read off, never as a member of its own. A part of
a figure is sent as the figure: its crop holds every part, and each part's own crop again took
a place of ANSWER_PICTURES for something already shown."""


def _item(record: neo4j.Record) -> RetrieverResultItem:
    """A passage as the model reads it -- it opens with its file and the headings above it,
    which is what an answer cites -- then the documents linked to its own, hop by hop, each
    with how it is linked and its passages nearest the question.

    Composed (NFC) throughout: macOS names a Korean file in loose jamo, and a model copying a
    name back that way broke it into letters no document on the page is called. An attachment
    is named as its reader knows it (`shown_name`): the answer cites what it reads. The
    metadata keeps the whole name, which tells two files apart."""
    files = {record["document_id"]: _composed(record["file"])}
    linked = [{**doc, "file": _composed(doc["file"])} for doc in record["linked"]]
    files.update({doc["document_id"]: doc["file"] for doc in linked})
    lines = [_document_line(record), record["text"]]
    for doc in linked:
        how = _HOW[doc["direction"]]
        lines.append(
            f"Linked document, hop {doc['hop']} ({files.get(doc['via'], doc['via'])} {how} it) "
            f"[{doc['file']}]:"
        )
        shown = doc["passages"][: LINKED_PASSAGES[doc["hop"]]]
        lines += [f"- {passage['text']}" for passage in shown]
    return RetrieverResultItem(
        content=shown_name(_composed("\n".join(lines))),
        metadata={
            "id": record["id"],
            "score": record["score"],
            "document_id": record["document_id"],
            "file": files[record["document_id"]],
            "members": record["members"],
            "linked": linked,
            "modified": record["modified"],
            "origin": mail_origin(record),
            "named": record.get("named", False),
            "closeness": record.get("closeness"),
            "versions": record.get("versions", []),
        },
    )


MAIL_CARRIERS = 3
"""How many of the messages that carried a document are named beside its passages."""


def mail_origin(record: Mapping[str, Any]) -> str | None:
    """Where a passage's document came from, when it came by mail: the message itself, or the
    messages that carried it (newest first, as the query returns them)."""
    if record.get("mail_message_id"):
        day = _day(record.get("mail_date") or "")
        return (f"Mail from {record.get('mail_from') or '?'}{' on ' + day if day else ''}"
                f", subject: {record.get('mail_subject') or ''}")
    if carriers := record.get("carriers"):
        return "Attached to: " + "; ".join(
            f"{c['subject'] or ''} ({c['sender'] or '?'}"
            f"{', ' + _day(c['date']) if c['date'] else ''})" for c in carriers)
    return None


def _document_line(record: Mapping[str, Any]) -> str:
    """What the passage's document is, ahead of the passage: its file and title, how it opens --
    where a byline, a source or a date stands -- the pages the passage is on, and when the file
    was changed. A passage past the first carried only the file and its headings, and a question
    naming the document's source, date or a page was answered "not in the context"."""
    head, _, opens = (record.get("opening") or "").partition("\n")
    # The first passage's own heading path (after the file) is how the document begins -- often
    # its title, not always -- so it is shown as the opening, not named a title.
    begins = " ".join(" ".join([head.split(" > ", 1)[1] if " > " in head else "", opens]).split())
    parts = [f"Document: {record['file']}"]
    if begins and not str(record["id"]).endswith(":0"):
        parts.append(f"Opens: {begins[:OPENING_CHARS]}")
    pages = [int(p) for p in record.get("pages") or []]
    if pages:
        first, last = pages[0], pages[-1]
        parts.append(f"page {first}" if first == last else f"pages {first}-{last}")
    if record.get("modified"):
        parts.append(f"Modified: {_day(record['modified'])}")
    if origin := mail_origin(record):
        parts.append(origin)
    return "[" + " | ".join(parts) + "]"


def _day(modified: object) -> str:
    """When a file was changed, as a date: the graph keeps it as seconds since 1970 (UTC)."""
    if isinstance(modified, int | float):
        return datetime.fromtimestamp(modified, UTC).date().isoformat()
    return str(modified)[:10]


def _composed(text: str) -> str:
    return unicodedata.normalize("NFC", text)


_HOW = {"cites": "cites", "cited_by": "is cited by", "similar": "is similar to"}
"""How the document a link starts from stands to the one it reaches, as the model reads it."""


def _source(item: RetrieverResultItem) -> GraphSource:
    found = dict(item.metadata or {})
    found.pop("named", None)  # read by `promoted` through `_named_in`
    found.pop("closeness", None)  # and through `_closeness_in`
    linked = [
        LinkedDocument(
            document_id=doc["document_id"], file=doc["file"], hop=doc["hop"], via=doc["via"],
            direction=doc["direction"], modified=doc["modified"],
            passages=[LinkedPassage(**{k: v for k, v in p.items() if k != "quote_of"})  # a quote's
                      # marker has done its work (`linked_without_answered_quotes`)
                      for p in doc["passages"][: LINKED_PASSAGES[doc["hop"]]]],
        )
        for doc in found.pop("linked", [])
    ]
    return GraphSource(context=item.content, linked=linked, **found)

"""Neo4j's two indexes over the passages -- vector (ANN) and fulltext (BM25) -- searched by our
own query and fused by weighted reciprocal rank fusion, with what a question's words and the
identifiers it names become as Lucene queries."""
from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Any

import neo4j
from neo4j_graphrag.retrievers import HybridCypherRetriever

from graphme.adapters.storage.workspace_scope import DEFAULT_SCOPE, WorkspaceScope
from graphme.application.corpus_links import document_numbers

_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def _lucene_escaped(text: str) -> str:
    """The question as plain words for the fulltext index, always: every Lucene special
    character escaped -- a `?` ending a Korean question is otherwise a wildcard, and a wildcard
    term skips the analyzer, so its last word never matched -- and the operators Lucene reads
    only in capitals (AND, OR, NOT) written in small letters. The vector search is given the
    question as it was asked."""
    escaped = "".join(f"\\{ch}" if ch in _LUCENE_SPECIAL else ch for ch in text)
    return re.sub(r"\b(AND|OR|NOT)\b", lambda m: m.group(1).lower(), escaped)


_IDENTIFIER = re.compile(r"(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[A-Za-z0-9][\w-]{2,}", re.ASCII)
"""A word of Latin letters and digits together, three characters or more -- QP-210, V50-0003,
ZX42 -- the way a document, a part or a lot is named; a plain number or a unit is not one."""


SEARCH_DEPTH = 400
"""Each search's candidates for the fusion, and the most passages a question's walk can go
down (`graph_rag.collected`). A passage only one search ranks is still fused, so each list
reaches well past what is shown -- the ANN search's recall at that depth bounds what the fusion
can find. The answer is often first in one search and far down the other: at 40 it was left
with one search's part and lost to passages middling in both. It is the same whatever is
asked for, so asking for fewer documents takes the front of the same ranking."""


def without_answered_quotes(found: Sequence[Any], *, in_space: set[str]) -> list[Any]:
    """`found` less the passages quoting a message that is itself in the space (`in_space`:
    Message-IDs): its own passages say it, and a reply is often read before what it quotes,
    so this is settled when a question is asked, not when the reply is read. A quote whose
    message is not in the space stays, or its words would be lost."""
    return [each for each in found if each.get("quote_of") not in in_space]


def linked_without_answered_quotes(linked: Sequence[dict[str, Any]], *, in_space: set[str]
                                   ) -> list[dict[str, Any]]:
    """Linked documents less their passages that quote a message in the space; a document
    with none left is not brought along."""
    kept = [{**doc, "passages": without_answered_quotes(doc["passages"], in_space=in_space)}
            for doc in linked]
    return [doc for doc in kept if doc["passages"]]


def documents_with_message_ids(driver: neo4j.Driver, ids: Sequence[str], *,
                               database: str | None = None,
                               scope: WorkspaceScope = DEFAULT_SCOPE) -> set[str]:
    """Those of the Message-IDs `ids` that a document in the workspace carries."""
    if not ids:
        return set()
    rows, _, _ = driver.execute_query(
        scope.cypher("MATCH (d:Document) WHERE d.meta_mail_message_id IN $ids "
                     "RETURN d.meta_mail_message_id AS id"), {"ids": list(ids)},
        database_=database, routing_=neo4j.RoutingControl.READ)
    return {row["id"] for row in rows}


class _OnceRetriever(HybridCypherRetriever):
    """Neo4j's two indexes -- vector (ANN) and fulltext (BM25) -- searched by our own query
    (`fused`), the rankings fused by weighted reciprocal rank fusion rather than the library's
    max-normalised scores; each document shown once across what a question finds (`_once`),
    and only passages something says are about the question (`_FUSED`). It keeps the library's
    retriever for its constructor, which checks the two indexes are there."""

    scope: WorkspaceScope = DEFAULT_SCOPE
    """The workspace searched: the indexes named are its own, and every pattern names it."""

    def __init__(self, driver: neo4j.Driver, *, floor: float, dense_weight: float, rrf_k: int,
                 text_only: bool = False, scope: WorkspaceScope = DEFAULT_SCOPE,
                 **kwargs: Any) -> None:
        super().__init__(driver, **kwargs)
        self.scope = scope
        self.text_only = text_only
        """The fulltext index alone (`_FUSED_WORDS`): passages embedded with another search
        model, which a question embedded with this one cannot be compared with."""
        self.floor = floor
        """`Settings.relevance_floor`."""
        self.dense_weight = dense_weight
        """`Settings.hybrid_dense_weight`."""
        self.rrf_k = rrf_k
        """`Settings.rrf_k`."""

    def fused(self, question: str, only: Collection[str] | None = None) -> list[neo4j.Record]:
        """The passages both searches rank, fused: a passage ranked r by a search counts that
        search's weight / (rrf_k + r), and its score is the sum, scaled so a passage first in
        both scores 1 -- the relative relevance the page shows. A passage is fused only with
        evidence it is about the question (`_FUSED`); none, and the search found nothing. A
        question naming a code leans on the words where some passage holds the code: an
        identifier is matched by its letters, and BM25 serves specific questions better than
        cosine (arXiv:2504.05324). A weight already leaning on the words stays as set.
        Highest score first: the query's later steps keep no order.

        `only` searches the passages of those documents alone: the two searches read
        SCOPED_REACH candidates and keep the chosen documents' within the query, so a chosen
        document ranked below SEARCH_DEPTH among all is still found."""
        dense, text = round(self.dense_weight, 6), round(1.0 - self.dense_weight, 6)
        # A code no passage holds says nothing about where the answer is (a page number dropped
        # as furniture, a code inside a picture): the question is searched as one naming none.
        named = named_identifiers(question)
        if named and dense > text and self._holds_any(named):
            dense, text = text, dense
        asked: dict[str, Any] = {
            "vector_index_name": self.vector_index_name,
            "fulltext_index_name": self.fulltext_index_name,
            "query_text": _lucene_escaped(question),
            "every_word": every_word(question), "floor": self.floor,
            "candidates": SEARCH_DEPTH,
            "dense_weight": dense, "text_weight": text, "rrf_k": self.rrf_k,
        }
        if not self.text_only:
            assert self.embedder is not None
            asked["query_vector"] = self.embedder.embed_query(question)
        fused = _FUSED_WORDS if self.text_only else _FUSED
        if only is not None:
            fused, asked = _scoped(fused), {**asked, "only": list(only), "reach": SCOPED_REACH}
        records, _, _ = self.driver.execute_query(
            self.scope.cypher(fused + self.retrieval_query),
            asked,
            database_=self.neo4j_database,
            routing_=neo4j.RoutingControl.READ,
        )
        held = documents_with_message_ids(
            self.driver, sorted({r["quote_of"] for r in records if r["quote_of"]}),
            database=self.neo4j_database, scope=self.scope)
        return sorted(without_answered_quotes(records, in_space=held),
                      key=lambda record: record["score"], reverse=True)


    def _holds_any(self, named: list[str]) -> bool:
        """Whether some passage holds one of the identifiers a question names."""
        records, _, _ = self.driver.execute_query(
            "CALL db.index.fulltext.queryNodes($index, $words, {limit: 1}) YIELD node "
            "RETURN count(node) AS found",
            {"index": self.fulltext_index_name,
             "words": any_named(named)},
            database_=self.neo4j_database,
            routing_=neo4j.RoutingControl.READ,
        )
        return bool(records and records[0]["found"])


_DENSE = """
  CALL db.index.vector.queryNodes($vector_index_name, $candidates, $query_vector)
    YIELD node, score
  WITH collect({node: node, near: 2 * score - 1 >= $floor}) AS ranked
  UNWIND range(0, size(ranked) - 1) AS at
  RETURN ranked[at].node AS node, $dense_weight / ($rrf_k + at + 1) AS part,
         ranked[at].near AS about
  UNION ALL"""
_WORDS = """
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
LIMIT $candidates
"""
_FUSED = "CALL () {" + _DENSE + _WORDS
_FUSED_WORDS = "CALL () {" + _WORDS
"""Weighted reciprocal rank fusion of Neo4j's two indexes over the passages, each searched to
`candidates`. Rank says nothing about whether anything was found -- every search has a first --
so a passage is kept only with evidence it is about the question: its cosine (Neo4j gives
(1 + cos) / 2) reaches `relevance_floor`, or it holds the question's words (`every_word`). A
fulltext match on some of them is not that: a nonsense question shares a common word with
something. What follows is the retrieval query, which reads `node` and `score`."""


SCOPED_REACH = 10_000
"""How many passages each search of a scoped question reads before keeping the chosen
documents'. ponytail: a flat reach; a corpus with far more passages needs the index to
filter by document."""


def _scoped(fused: str) -> str:
    """`fused` reading SCOPED_REACH candidates from each search and keeping the passages of
    the documents in `$only`."""
    return (fused.replace("$candidates, $query_vector)\n    YIELD node, score",
                          "$reach, $query_vector)\n    YIELD node, score\n"
                          "    WHERE node.document_id IN $only")
            .replace("{limit: $candidates})\n    YIELD node, score",
                     "{limit: $reach})\n    YIELD node, score\n"
                     "    WHERE node.document_id IN $only")
            .replace("{limit: $candidates})\n    YIELD node\n",
                     "{limit: $reach})\n    YIELD node\n    WHERE node.document_id IN $only\n"))


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
    return list(dict.fromkeys(
        [*numbers_named(question), *(w for w in words if _IDENTIFIER.fullmatch(w))]))


def numbers_named(question: str) -> list[str]:
    """The document numbers a question writes (`document_numbers`), without the particle
    written right after one ("SOP-2026-0412의")."""
    return [re.sub(r"[가-힣]+$", "", number) for number in sorted(document_numbers(question))]

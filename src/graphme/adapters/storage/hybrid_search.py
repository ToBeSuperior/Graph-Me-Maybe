"""Neo4j's two indexes over the passages -- vector (ANN) and fulltext (BM25) -- searched by our
own query and fused by weighted reciprocal rank fusion, with what a question's words and the
identifiers it names become as Lucene queries."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import neo4j
from neo4j_graphrag.retrievers import HybridCypherRetriever

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


SEARCH_DEPTH = 400
"""Each search's candidates for the fusion, and the most passages a question's walk can go
down (`graph_rag.collected`). A passage only one search ranks is still fused, so each list
reaches well past what is shown -- the ANN search's recall at that depth bounds what the fusion
can find. The answer is often first in one search and far down the other: at 40 it was left
with one search's part and lost to passages middling in both. It is the same whatever is
asked for, so asking for fewer documents takes the front of the same ranking."""


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

    def fused(self, question: str) -> list[neo4j.Record]:
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
                "candidates": SEARCH_DEPTH,
                "dense_weight": dense, "text_weight": text, "rrf_k": self.rrf_k,
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
LIMIT $candidates
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

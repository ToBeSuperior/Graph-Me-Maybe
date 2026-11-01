"""A question asks for documents: its passages are walked best first until that many documents
are reached, no document giving more than its share; the answer reads the front of them that
fits its budget. Invented passages."""
from __future__ import annotations

from neo4j_graphrag.types import RetrieverResultItem

from graphme.adapters.storage.graph_rag import (
    PASSAGES_PER_DOCUMENT,
    collected,
    filled,
    named_only,
    within_budget,
)


def _ranked(*docs: str) -> list[dict[str, str]]:
    return [{"id": f"{doc}:{n}", "document_id": doc} for n, doc in enumerate(docs)]


def test_the_walk_stops_at_the_passage_that_reaches_the_last_document() -> None:
    ranked = _ranked("a", "a", "b", "a", "c", "b", "d")

    taken = collected(ranked, 3)

    assert [p["id"] for p in taken] == ["a:0", "a:1", "b:2", "a:3", "c:4"]


def test_a_document_gives_no_more_than_its_share_and_the_walk_goes_on() -> None:
    ranked = _ranked(*["a"] * (PASSAGES_PER_DOCUMENT + 3), "b")

    taken = collected(ranked, 2)

    assert [p["document_id"] for p in taken] == ["a"] * PASSAGES_PER_DOCUMENT + ["b"]


def test_fewer_documents_take_the_front_of_the_same_walk() -> None:
    ranked = _ranked("a", "b", "a", "c", "b", "d", "e", "c", "f")

    for fewer in range(1, 6):
        assert collected(ranked, fewer) == collected(ranked, 6)[: len(collected(ranked, fewer))]


def test_fewer_documents_with_evidence_than_asked_give_what_there_is() -> None:
    assert [p["id"] for p in collected(_ranked("a", "b", "a"), 5)] == ["a:0", "b:1", "a:2"]


def _item(tokens: int) -> RetrieverResultItem:
    return RetrieverResultItem(content="word " * tokens, metadata={})


def test_the_answer_reads_the_front_that_fits_its_budget() -> None:
    items = [_item(400), _item(400), _item(400), _item(10)]

    read = within_budget(items, budget=1000)

    assert read == items[:2]


def test_the_best_passage_is_read_however_long() -> None:
    items = [_item(5000), _item(10)]

    assert within_budget(items, budget=1000) == items[:1]


def test_the_answer_reads_on_into_the_documents_found() -> None:
    """Past the walk, the answer goes on down the ranking in the documents it found -- a long
    document holding the answer in more sections than its share gives them -- never into one
    the walk did not reach."""
    ranked = _ranked(*["a"] * (PASSAGES_PER_DOCUMENT + 2), "b", "a", "c", "b")
    taken = collected(ranked, 2)

    read = filled(ranked, taken)

    assert read[: len(taken)] == taken
    assert [p["document_id"] for p in read[len(taken):]] == ["a", "a", "a", "b"]
    assert "c" not in {p["document_id"] for p in read}
    assert len({p["id"] for p in read}) == len(read)


def test_a_question_naming_documents_reads_only_in_them() -> None:
    """A question naming documents is answered from those documents' passages alone, best
    first and as many as the budget takes, not from the others the search also found."""
    ranked = _ranked("c", *["a"] * (PASSAGES_PER_DOCUMENT + 2), "b", "c", "a")

    read = named_only(ranked, {"a", "b"})

    assert [p["document_id"] for p in read] == ["a"] * (PASSAGES_PER_DOCUMENT + 2) + ["b", "a"]


def test_a_question_naming_nothing_is_not_narrowed() -> None:
    ranked = _ranked("a", "b", "c")

    assert named_only(ranked, set()) == ranked

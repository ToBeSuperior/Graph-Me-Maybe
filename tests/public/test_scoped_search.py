"""A question about chosen documents: only their passages, and only links inside them."""
from __future__ import annotations

from graphme.adapters.storage.graph_rag import collected, linked_within, linked_without, within


def _ranked() -> list[dict[str, str]]:
    return [{"id": f"{d}:{n}", "document_id": d} for n in range(3) for d in "abc"]


def test_only_keeps_passages_of_the_chosen_documents() -> None:
    taken = collected(within(_ranked(), frozenset({"b"})), 3)

    assert taken and {p["document_id"] for p in taken} == {"b"}
    assert len(within(_ranked(), None)) == 9  # no scope: everything


def test_linked_documents_outside_the_scope_are_not_brought() -> None:
    linked = [{"document_id": "a", "hop": 2, "via": "s"},
              {"document_id": "z", "hop": 2, "via": "s"},
              {"document_id": "a", "hop": 3, "via": "z"}]

    assert linked_within(linked, frozenset({"a"})) == [linked[0]]  # not hop 3 through z
    assert linked_within(linked, None) == linked


def test_only_naming_nothing_in_the_graph_finds_nothing() -> None:
    from types import SimpleNamespace

    from graphme.adapters.storage.graph_rag import GraphRag

    rag = GraphRag(SimpleNamespace(driver=None, version_pairs=list), embedder=None,  # type: ignore[arg-type]
                   endpoint=None, client=None, model="m", dimensions=3,  # type: ignore[arg-type]
                   relevance_floor=0.0, dense_weight=0.6, rrf_k=60, wait=lambda _: None)
    rag._asked = lambda question, history=(): (("q",), (), "q")  # type: ignore[method-assign]
    rag._shared = lambda key, work: _ranked()  # type: ignore[method-assign]

    assert rag._found("invented", 3, only=frozenset({"deleted-since"})) == []


def test_a_chosen_document_below_the_unscoped_depth_is_still_found() -> None:
    from types import SimpleNamespace

    from graphme.adapters.storage.hybrid_search import (
        _FUSED,
        SCOPED_REACH,
        SEARCH_DEPTH,
        _OnceRetriever,
    )

    sent: list[tuple[str, dict[str, object]]] = []

    class Driver:
        def execute_query(self, query: str, params: dict[str, object], **_: object) -> tuple:
            sent.append((query, params))
            return [], None, None

    retriever = object.__new__(_OnceRetriever)  # the constructor checks real indexes
    retriever.__dict__.update(
        driver=Driver(), floor=0.0, dense_weight=0.6, rrf_k=60, vector_index_name="v",
        fulltext_index_name="t", retrieval_query="RETURN 1", embedder=None, text_only=True,
        scope=SimpleNamespace(cypher=lambda q: q), neo4j_database=None)
    retriever.fused("invented", {"b"})

    query, params = sent[0]
    assert params["only"] == ["b"] and params["reach"] == SCOPED_REACH > SEARCH_DEPTH
    assert query.count("WHERE node.document_id IN $only") == 2  # each search keeps the chosen
    assert "{limit: $reach}" in query and "WHERE node.document_id IN $only" not in _FUSED


def test_linked_documents_left_out_are_not_brought_nor_what_hangs_from_them() -> None:
    linked = [{"document_id": "a", "hop": 2, "via": "s"},
              {"document_id": "z", "hop": 2, "via": "s"},
              {"document_id": "y", "hop": 3, "via": "z"}]

    assert linked_without(linked, frozenset({"z"})) == [linked[0]]
    assert linked_without(linked, frozenset()) == linked


def test_a_document_left_out_has_no_passage_found() -> None:
    from types import SimpleNamespace

    from graphme.adapters.storage.graph_rag import GraphRag

    rag = GraphRag(SimpleNamespace(driver=None, version_pairs=list), embedder=None,  # type: ignore[arg-type]
                   endpoint=None, client=None, model="m", dimensions=3,  # type: ignore[arg-type]
                   relevance_floor=0.0, dense_weight=0.6, rrf_k=60, wait=lambda _: None)
    rag._asked = lambda question, history=(): (("q",), (), "q")  # type: ignore[method-assign]
    rag._shared = lambda key, work: _ranked()  # type: ignore[method-assign]
    rag._linked = lambda found, question, only=None, exclude=frozenset(): found  # type: ignore[method-assign]

    found = rag._found("invented", 3, exclude=frozenset({"b"}))
    assert found and {p["document_id"] for p in found} == {"a", "c"}

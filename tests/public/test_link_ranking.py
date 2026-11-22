"""A linked document ranks by its own passages: nearer the question than a found one, it moves
up among them; far, it stays under the document it was reached from. Nearness is the same
measure on both sides -- each passage's closeness to the question's vector -- never a found
passage's fused score."""
from __future__ import annotations

from graphme.adapters.storage.graph_rag import (
    GraphSource,
    LinkedDocument,
    LinkedFrom,
    LinkedPassage,
    promoted,
)


def _linked(doc: str, near: float, *, hop: int = 2, via: str = "wo") -> LinkedDocument:
    return LinkedDocument(document_id=doc, file=f"{doc}.docx", hop=hop, via=via,
                          direction="cites",
                          passages=[LinkedPassage(f"{doc}:0", near, [f"{doc}-u"], f"{doc} text")])


def _found(doc: str, linked: list[LinkedDocument] | None = None) -> GraphSource:
    # A fused score near 1 for every found passage: ranks within a search, it says nothing
    # of how near the question a passage is.
    return GraphSource(id=f"{doc}:0", score=0.99, document_id=doc, file=f"{doc}.pdf",
                       members=[], context=doc, linked=linked or [])


def _near(**closeness: float) -> dict[str, float]:
    return {f"{doc}:0": near for doc, near in closeness.items()}


def _shown(sources: list[GraphSource]) -> set[str]:
    return {s.document_id for s in sources} | {d.document_id for s in sources for d in s.linked}


def test_a_linked_document_nearer_than_a_found_one_moves_up() -> None:
    sources = [_found("wo", [_linked("procedure", 0.8)]), _found("memo")]

    ranked = promoted(sources, 0.85, set(), _near(wo=0.9, memo=0.6))

    assert [s.document_id for s in ranked] == ["wo", "procedure", "memo"]
    moved = ranked[1]
    assert moved.id == "procedure:0" and moved.context == "procedure text"
    assert moved.promoted_from == LinkedFrom(via="wo", hop=2, direction="cites")
    assert ranked[0].linked == []  # no longer under its parent


def test_a_far_linked_document_stays_under_its_parent() -> None:
    far = _linked("far", 0.8, hop=3, via="procedure")  # 0.8 * 0.85^2 < 0.6
    sources = [_found("wo", [_linked("procedure", 0.7), far]), _found("memo")]

    ranked = promoted(sources, 0.85, set(), _near(wo=0.9, memo=0.6))

    assert [s.document_id for s in ranked] == ["wo", "memo"]
    assert [d.document_id for d in ranked[0].linked] == ["procedure", "far"]


def test_a_named_linked_document_is_not_discounted() -> None:
    sources = [_found("wo", [_linked("procedure", 0.65)]), _found("memo")]
    near = _near(wo=0.9, memo=0.6)

    assert [s.document_id for s in promoted(sources, 0.85, set(), near)] == ["wo", "memo"]
    named = promoted(sources, 0.85, {"procedure"}, near)
    assert [s.document_id for s in named] == ["wo", "procedure", "memo"]


def test_a_search_by_words_alone_moves_nothing() -> None:
    sources = [_found("wo", [_linked("procedure", 0.95)]), _found("memo")]

    ranked = promoted(sources, 1.0, set(), {})  # no question vector: no closeness to compare

    assert [s.document_id for s in ranked] == ["wo", "memo"]
    assert [d.document_id for d in ranked[0].linked] == ["procedure"]


def test_the_number_of_documents_asked_for_is_kept() -> None:
    sources = [_found("wo", [_linked("procedure", 0.8), _linked("ks", 0.3),
                             _linked("std", 0.85, hop=3, via="procedure")]),
               _found("memo", [_linked("sheet", 0.95, via="memo")])]

    ranked = promoted(sources, 0.85, set(), _near(wo=0.9, memo=0.6))

    assert _shown(ranked) == _shown(sources)
    assert len(ranked) == len(sources) + 3  # procedure, sheet, std moved; ks stays linked
    # a hop-3 document stays reachable from the hop-2 one it came through
    assert all(d.via in _shown(ranked) for s in ranked for d in s.linked)


def test_several_workspaces_keep_every_document_a_promoted_one_stood_among() -> None:
    from graphme.adapters.storage.graph_rag import find_across

    ranked = promoted([_found("wo", [_linked("procedure", 0.95)]), _found("memo")], 0.85, set(),
                      _near(wo=0.9, memo=0.6))
    assert [s.document_id for s in ranked] == ["wo", "procedure", "memo"]

    fused = find_across([("w1", ranked)], 2, rrf_k=60)

    # two documents asked for are two found ones: the promoted one goes with its parent
    assert [s.document_id for s in fused] == ["wo", "procedure", "memo"]
    assert {s.workspace for s in fused} == {"w1"}


def test_a_promoted_document_whose_parent_was_cut_goes_with_it() -> None:
    from graphme.adapters.storage.graph_rag import find_across

    ranked = promoted([_found("wo"), _found("memo", [_linked("sheet", 0.95, via="memo")])],
                      0.85, set(), _near(wo=0.7, memo=0.6))
    assert [s.document_id for s in ranked] == ["sheet", "wo", "memo"]

    assert [s.document_id for s in find_across([("w1", ranked)], 1, rrf_k=60)] == ["wo"]


def test_an_answer_ranks_by_what_its_items_carry() -> None:
    """`written` reads whether the question names a document and how near each found passage
    is from the items themselves (`_marked`, `_closeness_in`), as `ask_across` hands them on."""
    from types import SimpleNamespace

    import neo4j

    from graphme.adapters.storage.graph_rag import GraphRag, _item, _marked

    def record(doc: str, near: float, linked: list[dict[str, object]]) -> neo4j.Record:
        return neo4j.Record({"id": f"{doc}:0", "score": 0.99, "document_id": doc,  # type: ignore[no-untyped-call]
                             "file": f"{doc}.pdf", "text": doc, "members": [],
                             "modified": None, "closeness": near, "linked": linked})

    procedure = {"document_id": "procedure", "file": "procedure.docx", "hop": 2, "via": "wo",
                 "direction": "cites", "modified": None,
                 "passages": [{"id": "procedure:0", "score": 0.65, "members": [], "text": "p"}]}
    records = [record("wo", 0.9, [procedure]), record("memo", 0.6, [])]
    rag = GraphRag(SimpleNamespace(driver=None), embedder=None,  # type: ignore[arg-type]
                   endpoint=None, client=None, model="m", dimensions=3,  # type: ignore[arg-type]
                   relevance_floor=0.0, dense_weight=0.6, rrf_k=60, link_discount=0.85)
    rag.answered = lambda *_: "an answer"  # type: ignore[method-assign]

    def sources(named: set[str]) -> list[str]:
        items = [_item(r) for r in _marked(records, named)]
        return [s.document_id for s in rag.written("q", items, []).sources]

    assert sources(set()) == ["wo", "memo"]  # 0.65 * 0.85 is not nearer than 0.6
    assert sources({"procedure"}) == ["wo", "procedure", "memo"]  # named: no discount

"""Copies and versions of a document are one family, told by what they say: the same bytes, or
passages that are mostly the same. A search shows a family once, by its best-ranked member, and
the others fold under it; a document a question is scoped to is never folded away."""
from __future__ import annotations

import neo4j

from graphme.adapters.storage.graph_rag import _once
from graphme.application.versions import (
    VERSION_OVERLAP,
    Measured,
    Member,
    VersionOf,
    by_document,
    families,
    fold_versions,
    minhash,
    overlap,
    passage_hashes,
    passage_keys,
)

HASH_A, HASH_B, HASH_C = ("a" * 64, "b" * 64, "c" * 64)

_POLICY = [f"Gate rules.docx > Hours\nSection {n}: the invented harbour office opens gate {n}."
           for n in range(12)]


def test_a_version_with_one_passage_changed_is_one_family_with_the_first() -> None:
    edited = [*_POLICY[:-1], "Gate rules v2.docx > Hours\nSection 11: the office closes at noon."]
    unrelated = [f"Cake.docx > Steps\nRecipe step {n}: fold the invented dough {n} times."
                 for n in range(12)]
    docs = [Measured("v1", HASH_A, frozenset(passage_hashes(_POLICY))),
            Measured("v2", HASH_B, frozenset(passage_hashes(edited))),
            Measured("cake", HASH_C, frozenset(passage_hashes(unrelated)))]

    assert overlap(_POLICY, edited) >= VERSION_OVERLAP
    assert families(docs) == [{"v1", "v2"}]


def test_a_renamed_identical_file_is_one_family_by_its_bytes() -> None:
    # The same bytes under another name: each passage opens with its own file name.
    copy = [t.replace("Gate rules.docx", "Gate rules (copy).docx") for t in _POLICY]
    first = frozenset(k for t in _POLICY for k in passage_keys(t))
    renamed = frozenset(k for t in copy for k in passage_keys(t))

    assert first == renamed
    assert families([Measured("one", HASH_A, frozenset()),
                     Measured("copy", HASH_A, frozenset())]) == [{"one", "copy"}]


def test_two_documents_sharing_a_boilerplate_paragraph_stay_apart() -> None:
    boiler = "This invented document is the property of the harbour office."
    memo = [f"Memo.docx\n{boiler}",
            *(f"Memo.docx > Cranes\nMemo line {n}: crane {n} is serviced." for n in range(4))]
    order = [f"Order.docx\n{boiler}",
             *(f"Order.docx > Rope\nOrder line {n}: buy invented rope {n}." for n in range(4))]

    assert overlap(memo, order) < VERSION_OVERLAP
    assert families([Measured("memo", HASH_A, frozenset(passage_hashes(memo))),
                     Measured("order", HASH_B, frozenset(passage_hashes(order)))]) == []


def test_a_document_with_an_annex_appended_is_one_family_with_the_original() -> None:
    annex = [f"Gate rules final.docx > Annex\nAnnex row {n}: invented tariff {n} for gate {n}."
             for n in range(15)]
    annexed = [t.replace("Gate rules.docx", "Gate rules final.docx") for t in _POLICY] + annex
    docs = [Measured("v1", HASH_A, frozenset(passage_hashes(_POLICY))),
            Measured("annexed", HASH_B, frozenset(passage_hashes(annexed)))]

    assert overlap(passage_hashes(_POLICY), passage_hashes(annexed)) < 0.5  # not by Jaccard alone
    assert families(docs) == [{"v1", "annexed"}]


def test_a_short_document_quoted_inside_a_long_one_stays_apart() -> None:
    clause = [f"Clause.docx > Terms\nTerm {n}: the invented harbour office keeps the key."
              for n in range(3)]
    report = [t.replace("Clause.docx", "Report.docx") for t in clause] + [
        f"Report.docx > Findings\nFinding {n}: invented crane {n} was late." for n in range(20)]

    assert families([Measured("clause", HASH_A, frozenset(passage_hashes(clause))),
                     Measured("report", HASH_B, frozenset(passage_hashes(report)))]) == []


def test_minhash_is_the_same_for_the_same_passages_and_nothing_overlaps_with_nothing() -> None:
    assert minhash(_POLICY) == minhash(list(reversed(_POLICY)))
    assert len(minhash(_POLICY)) > 0
    assert overlap([], []) == 0.0


# Folding a search ----------------------------------------------------------------------------


_V1 = Member("v1", "Gate rules.docx", 100.0)
_V2 = Member("v2", "Gate rules v2.docx", 200.0)
_FAMILY = by_document([(_V1, _V2)])


def _passage(doc: str, n: int = 0) -> dict[str, object]:
    return {"id": f"{doc}:{n}", "document_id": doc}


def test_a_family_is_shown_by_its_best_ranked_member() -> None:
    ranked = [_passage("v1"), _passage("memo"), _passage("v2"), _passage("v1", 1)]

    assert [p["id"] for p in fold_versions(ranked, _FAMILY)] == ["v1:0", "memo:0", "v1:1"]


def test_a_shown_member_carries_the_others_with_the_latest_marked() -> None:
    def record(doc: str, linked: list[dict[str, object]]) -> neo4j.Record:
        return neo4j.Record({"id": f"{doc}:0", "document_id": doc,  # type: ignore[no-untyped-call]
                             "linked": linked})

    copy = {"document_id": "v2", "file": "Gate rules v2.docx", "hop": 2, "via": "memo",
            "direction": "similar", "passages": [{"id": "v2:0", "score": 0.9}]}
    walked = _once([record("v1", []), record("memo", [copy])], family=_FAMILY)

    assert walked[0]["versions"] == [VersionOf("v2", "Gate rules v2.docx", 200.0, latest=True)]
    assert walked[1]["linked"] == [], "a version of a shown document is not brought along"


def test_a_question_scoped_to_several_members_shows_each() -> None:
    ranked = [_passage("v1"), _passage("v2")]

    assert fold_versions(ranked, _FAMILY, only=frozenset({"v1", "v2"})) == ranked
    # one member alone in the passages stands for its family
    assert fold_versions([_passage("v2")], _FAMILY) == [_passage("v2")]


def test_an_ungrouped_pair_stays_split_when_regrouped_or_read_again() -> None:
    from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
    from tests.fakes.documents import named_document
    from tests.public.test_scoped_queries import ANSWERS, SCOPE, _Recording

    driver = _Recording()
    space = Neo4jGraphSpace(driver, scope=SCOPE)  # type: ignore[arg-type]
    ANSWERS["RETURN DISTINCT o.id AS other"] = [{"other": "cd"}]
    try:
        space.put_document(named_document("ab", "invented.pdf"))
    finally:
        del ANSWERS["RETURN DISTINCT o.id AS other"]
    kept = [p for q, p in driver.sent if "{split_by_user: true}" in q]
    assert kept and kept[0]["others"] == ["cd"], "read again, the split is written back"

    driver.sent.clear()
    space.group_versions()
    cleared = [q for q, _ in driver.sent if "SIMILAR_VERSION" in q and "DELETE" in q]
    assert cleared and all("split_by_user IS NULL" in q for q in cleared)


def test_an_ungrouped_pair_stays_apart_after_one_side_is_rebuilt() -> None:
    # rules-2026.docx (c1, ungrouped from c0) is edited: its new reading (c2) replaces c1
    from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
    from tests.fakes.documents import named_document
    from tests.public.test_scoped_queries import ANSWERS, SCOPE, _Recording

    driver = _Recording()
    space = Neo4jGraphSpace(driver, scope=SCOPE)  # type: ignore[arg-type]
    held = {"AS replaces": [{"id": "c1", "replaces": ["c-older"]}],
            "RETURN DISTINCT o.id AS other": [{"other": "c0"}]}
    ANSWERS.update(held)
    try:
        space.put_document(named_document("c2", "rules-2026.docx"))
    finally:
        for key in held:
            del ANSWERS[key]

    asked = [p for q, p in driver.sent if "RETURN DISTINCT o.id AS other" in q]
    assert asked and sorted(asked[0]["ids"]) == ["c1", "c2"], "the old reading's splits are read"
    kept = [p for q, p in driver.sent if "{split_by_user: true}" in q]
    assert kept and kept[0]["others"] == ["c0"], "and written for the new one"
    lineage = [p for q, p in driver.sent if "d.replaces = $replaces" in q]
    assert lineage and lineage[0]["replaces"] == ["c-older", "c1"]


def test_copies_changed_at_the_same_time_keep_one_latest_whatever_their_ids() -> None:
    from graphme.application.versions import Member, latest

    one, two = Member("x1", "Offer.pdf", 5.0), Member("x2", "Offer (1).pdf", 5.0)
    assert latest([one, two]).file == latest([Member("y9", "Offer.pdf", 5.0),
                                              Member("y1", "Offer (1).pdf", 5.0)]).file
    assert latest([one, two]) == latest([two, one])


def test_one_turn_reads_the_families_and_identities_once() -> None:
    from types import SimpleNamespace

    import httpx

    from graphme.adapters.storage.graph_rag import GraphRag, one_search

    calls = {"pairs": 0, "identities": 0}

    def pairs() -> list[object]:
        calls["pairs"] += 1
        return []

    def identities() -> list[object]:
        calls["identities"] += 1
        return []

    rag = GraphRag(SimpleNamespace(driver=None, version_pairs=pairs, identities=identities),  # type: ignore[arg-type]
                   embedder=None, endpoint=None, client=httpx.Client(), model="m",  # type: ignore[arg-type]
                   dimensions=3, relevance_floor=0.0, dense_weight=0.6, rrf_k=60)
    with one_search():  # what find and read, side by side, each ask for
        for _ in range(2):
            rag._families()
            rag._named(("Invented memo",))

    assert calls == {"pairs": 1, "identities": 1}

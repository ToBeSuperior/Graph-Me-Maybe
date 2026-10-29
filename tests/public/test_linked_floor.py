"""A linked document is brought along only for passages that come near the question, the same
floor a passage found must reach. Invented links."""
from __future__ import annotations

import neo4j

from graphme.adapters.storage.graph_rag import _once, relevant_links


def _doc(doc_id: str, hop: int, via: str, *cosines: float) -> dict:
    return {"document_id": doc_id, "file": f"{doc_id}.docx", "hop": hop, "via": via,
            "direction": "cites",
            "passages": [{"id": f"{doc_id}:{n}", "score": (1 + c) / 2, "members": [], "text": ""}
                         for n, c in enumerate(cosines)]}


def test_a_linked_document_keeps_only_its_passages_past_the_floor() -> None:
    [kept] = relevant_links([_doc("b", 2, "a", 0.6, 0.2)], floor=0.4)

    assert [p["id"] for p in kept["passages"]] == ["b:0"]


def test_a_linked_document_with_nothing_near_the_question_is_not_brought_along() -> None:
    linked = [_doc("b", 2, "a", 0.3, 0.1), _doc("c", 2, "a", 0.5)]

    assert [d["document_id"] for d in relevant_links(linked, floor=0.4)] == ["c"]


def test_what_a_dropped_document_links_to_is_not_reached_through_it() -> None:
    linked = [_doc("b", 2, "a", 0.1), _doc("d", 3, "b", 0.9)]
    found = neo4j.Record({"id": "a:0", "score": 1.0, "document_id": "a", "file": "a.docx",
                          "text": "", "members": [], "linked": relevant_links(linked, 0.4)})

    [record] = _once([found], each_folder=True)

    assert record["linked"] == []

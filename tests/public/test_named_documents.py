"""A question that names a document -- by its number, its file name or its title, however
briefly -- has that document's passages first, when the search found it. Invented documents."""
from __future__ import annotations

from graphme.adapters.storage.graph_rag import named_first, named_phrases
from graphme.application.corpus_links import DocumentIdentity, documents_named

LIBRARY = [
    DocumentIdentity("a", "QA-2025-0101_누설 검사 기준.docx", "누설 검사 기준"),
    DocumentIdentity("b", "QA-2025-0102_누설 검사 절차서.docx", "누설 검사 절차서"),
    DocumentIdentity("c", "회의 메모.docx", "주간 회의 메모"),
    DocumentIdentity("d", "TimeAware_review.docx", "Reviews of TimeAware"),
    DocumentIdentity("e", "불량 분석 보고서.pptx", None),
    DocumentIdentity("f", "설치 보고서.pdf", None),
]


def test_a_number_names_its_document() -> None:
    assert documents_named(["QA-2025-0101"], LIBRARY) == {"a"}


def test_a_name_or_title_held_whole_by_one_document_names_it() -> None:
    assert documents_named(["누설 검사 기준"], LIBRARY) == {"a"}
    assert documents_named(["TimeAware"], LIBRARY) == {"d"}
    assert documents_named(["주간 회의"], LIBRARY) == {"c"}


def test_a_phrase_many_documents_hold_names_none_of_them() -> None:
    assert documents_named(["누설 검사"], LIBRARY) == set()
    assert documents_named(["보고서"], LIBRARY) == set()


def test_a_word_any_document_could_be_called_names_nothing() -> None:
    assert documents_named(["문서", "0101", "첨부 파일"], LIBRARY) == set()


def test_the_named_documents_passages_come_first_in_their_order() -> None:
    ranked = [{"id": n, "document_id": d} for n, d in
              enumerate(["x", "a", "y", "a", "z"])]

    moved = named_first(ranked, {"a"})

    assert [p["id"] for p in moved] == [1, 3, 0, 2, 4]
    assert named_first(ranked, set()) == ranked
    assert named_first(ranked, {"q"}) == ranked  # named but not found: nothing moves


def test_a_number_in_the_question_is_seen_without_the_model() -> None:
    assert named_phrases("SOP-2026-0412의 토크 기준은?", []) == ("sop-2026-0412",)
    assert named_phrases("TimeAware 리뷰 요약", ["TimeAware"]) == ("TimeAware",)
    assert named_phrases("2026-10-29 회의 내용", []) == ()

"""A short number written alone ("per QP-210") names a document when a document in the space is
filed under it -- its file name starts with it -- whatever its digits; one nothing is filed
under ("SHA-256") names nothing. A name led by a short number is kept even when its cue is only
itself, and judged as a name. Invented documents."""
from __future__ import annotations

from graphme.application.corpus_links import DocumentIdentity, Mention, link_mentions
from graphme.application.document_references import _written
from graphme.domain.models import DocumentReference

SPACE = [
    DocumentIdentity("spec", "GS-100_Valve Part Basic Specification.docx"),
    DocumentIdentity("proc", "QP-210_Leak Test Procedure.docx"),
    DocumentIdentity("record", "Leak Test Record.xlsx"),
    DocumentIdentity("notes", "Notes on SHA-256 hashing.docx"),
]


def _targets(name: str, own: str = "record") -> set[str]:
    return {edge.target_id for edge in link_mentions([Mention("m", own, name)], SPACE)}


def test_a_short_number_names_the_document_filed_under_it() -> None:
    assert _targets("QP-210") == {"proc"}
    assert _targets("GS-100 Valve Part Basic Specification", own="proc") == {"spec"}


def test_a_short_number_nothing_is_filed_under_names_nothing() -> None:
    assert _targets("SHA-256") == set()  # in a file name, but not what it is filed under
    assert _targets("COVID-19") == set()


def test_a_short_number_with_other_words_is_judged_as_a_name() -> None:
    assert _targets("QP-210 Leak Test Plan") == set()  # another document filed alike


def test_a_name_led_by_a_number_is_kept_with_itself_as_its_cue() -> None:
    reference = DocumentReference(subject_id="u", name="GS-100 Valve Part Basic Specification",
                                  cue="GS-100 Valve Part Basic Specification", kind="named",
                                  confidence=0.9)
    assert _written(reference, "Materials follow GS-100 Valve Part Basic Specification.")

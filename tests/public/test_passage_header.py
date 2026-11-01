"""A found passage reaches the answer with what its document is: the title and opening lines
(where a byline or a date stands), the pages the passage is on, and when the file was changed.
A passage deep in a document otherwise carried only the file name and its headings, and a
question naming the document's source or a page was answered "not in the context"."""
from __future__ import annotations

from graphme.adapters.storage.graph_rag import _item


def _record(**over: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "doc-1:7", "score": 0.9, "document_id": "doc-1", "file": "weekly-notes.docx",
        "text": "weekly-notes.docx > Weekly notes > Budget\nThe plan moved to May.",
        "members": ["u1"], "linked": [], "modified": "2031-04-02T09:30:00",
        "opening": ("weekly-notes.docx > Weekly notes\n"
                    "Harbor Desk · 2031-04-01 08:15 · J. Doe\nIntro line."),
        "pages": [3, 4],
    }
    record.update(over)
    return record


def test_a_passage_opens_with_its_document_title_opening_pages_and_date() -> None:
    content = _item(_record()).content  # type: ignore[arg-type]

    head, _, body = content.partition("\n")
    assert "weekly-notes.docx" in head
    assert "Weekly notes" in head
    assert "Harbor Desk · 2031-04-01 08:15 · J. Doe" in head
    assert "pages 3-4" in head
    assert "2031-04-02" in head
    assert body.startswith("weekly-notes.docx > Weekly notes > Budget")


def test_the_first_passage_does_not_repeat_its_own_opening() -> None:
    first = _record(id="doc-1:0", text="weekly-notes.docx > Weekly notes\nHarbor Desk")
    content = _item(first).content  # type: ignore[arg-type]

    assert "Opens:" not in content.partition("\n")[0]


def test_an_opening_is_not_called_a_title() -> None:
    content = _item(_record(opening="report.pdf > Contents\nMission Profile")).content  # type: ignore[arg-type]

    assert "Title" not in content.partition("\n")[0]
    assert "Opens: Contents Mission Profile" in content


def test_one_page_and_no_page_are_said_plainly() -> None:
    one = _item(_record(pages=[5, 5])).content  # type: ignore[arg-type]
    none = _item(_record(pages=[])).content  # type: ignore[arg-type]

    assert "page 5" in one.partition("\n")[0]
    assert "page" not in none.partition("\n")[0].replace("weekly-notes", "")

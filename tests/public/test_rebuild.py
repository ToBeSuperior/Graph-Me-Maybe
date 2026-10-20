"""A file edited and built again replaces what was read from it before. Invented documents."""
from __future__ import annotations

from pathlib import Path

from graphme.application.corpus_build import build_graph
from graphme.domain.models import ExtractedDocument
from tests.fakes.documents import named_document


class _Space:
    """The space as the build sees it: documents by id, each with the file it was read from."""

    def __init__(self) -> None:
        self.documents: dict[str, ExtractedDocument] = {}

    def put_document(self, extracted: ExtractedDocument) -> None:
        self.documents[extracted.document.id] = extracted

    def delete_documents(self, document_ids: list[str]) -> None:
        for document_id in document_ids:
            self.documents.pop(document_id, None)

    def read_from(self, source_path: str) -> set[str]:
        return {d for d, doc in self.documents.items()
                if str(doc.document.source_path) == source_path}

    def contents(self, hashes: list[str]) -> set[str]:
        held = {doc.document.content_hash for doc in self.documents.values()}
        return {h for h in hashes if h in held}

    def relocate(self, content_hash: str, source_path: str) -> None:
        for document_id, doc in self.documents.items():
            if doc.document.content_hash == content_hash:
                record = doc.document.model_copy(update={"source_path": Path(source_path)})
                self.documents[document_id] = doc.model_copy(update={"document": record})

    def identities(self) -> tuple:
        return ()

    def mentions(self) -> tuple:
        return ()

    def unlinked(self, rule: str) -> set[str]:
        return set()

    def mark_linked(self, documents: list[str], rule: str) -> None:
        pass

    def put_links(self, links: list, *, of: list) -> None:
        pass


def test_a_file_edited_and_built_again_leaves_only_its_new_reading() -> None:
    # The edited file has a new content hash, so a new id; the reading of the old content
    # stayed beside it, and answers wavered between the two values.
    space = _Space()
    first, edited = named_document("a", "procedure.docx"), named_document("b", "procedure.docx")

    build_graph([Path("procedure.docx")], process=lambda _: first, space=space)  # type: ignore[arg-type]
    build_graph([Path("procedure.docx")], process=lambda _: edited, space=space)  # type: ignore[arg-type]

    assert list(space.documents) == ["b"]


def test_another_file_of_the_same_name_elsewhere_is_left_alone() -> None:
    space = _Space()
    here = named_document("a", "procedure.docx")
    there = named_document("b", "procedure.docx").model_copy(deep=True)
    there = there.model_copy(update={"document": there.document.model_copy(
        update={"source_path": Path("other") / "procedure.docx"})})

    build_graph([Path("procedure.docx")], process=lambda _: here, space=space)  # type: ignore[arg-type]
    build_graph([Path("other/procedure.docx")], process=lambda _: there, space=space)  # type: ignore[arg-type]

    assert sorted(space.documents) == ["a", "b"]


def test_a_file_moved_to_another_folder_is_found_where_it_is_now(tmp_path: Path) -> None:
    # Its content was known, so it was passed over -- and the space kept naming the folder it
    # had left, where neither its pages nor its download could be found again.
    from graphme.application.corpus_build import content_hash

    moved = tmp_path / "moved" / "procedure.docx"
    moved.parent.mkdir()
    moved.write_bytes(b"invented procedure")
    space = _Space()
    read = named_document("a", "procedure.docx")
    record = read.document.model_copy(update={
        "content_hash": content_hash(moved), "source_path": tmp_path / "procedure.docx"})
    space.put_document(read.model_copy(update={"document": record}))

    report = build_graph([moved], process=lambda _: read, space=space)  # type: ignore[arg-type]

    assert report.known == 1
    assert space.documents["a"].document.source_path == moved

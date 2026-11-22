"""Copies and versions of a document on the way out: a found document carries the versions
folded under it, the library shows a family as one row under its latest file, and a document
ungrouped stands on its own."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from graphme.adapters.storage.graph_rag import GraphSource
from graphme.application.versions import Member, VersionOf
from graphme.server.state import Demo
from tests.public.server.conftest import FakeSpace

_DRAFT = Member("draft", "Invented quay rules.docx", 1_600_000_000.0)
_FINAL = Member("final", "Invented quay rules final.docx", 1_700_000_000.0)


class _VersionedRag:
    text_only = False

    def find(self, question: str, *, documents: int, **_: Any) -> list[GraphSource]:
        return [GraphSource(id="draft:0", score=0.9, document_id="draft", file=_DRAFT.file,
                            members=[], context="quay rules",
                            versions=[VersionOf("final", _FINAL.file, _FINAL.modified, True)])]

    def last_queries(self, question: str, history: Any = ()) -> tuple[str, ...]:
        return (question,)


def test_a_found_document_carries_its_folded_versions(demo: Demo, client: TestClient) -> None:
    demo.graph._rag = _VersionedRag()

    found = client.post("/v1/search", json={"question": "quay rules?"}).json()

    assert found["sources"][0]["versions"] == [
        {"document_id": "final", "file": _FINAL.file, "modified": _FINAL.modified,
         "latest": True}]


def _library(space: FakeSpace) -> None:
    space.documents = [
        {"id": d.document_id, "label": d.file, "title": None, "mentions": 0,
         "modified_at": d.modified}
        for d in (_DRAFT, _FINAL)
    ] + [{"id": "memo", "label": "memo.pdf", "title": None, "mentions": 0}]
    space.versions = [(_DRAFT, _FINAL)]


def test_the_library_shows_a_family_as_its_latest_file(
    space: FakeSpace, client: TestClient
) -> None:
    _library(space)

    items = {i["name"]: i for i in client.get("/v1/documents").json()["items"]}

    assert set(items) == {_FINAL.file, "memo.pdf"}
    assert items[_FINAL.file]["versions"] == [
        {"document_id": "draft", "file": _DRAFT.file, "modified": _DRAFT.modified,
         "latest": False}]
    assert items["memo.pdf"]["versions"] == []


def test_an_ungrouped_document_stands_on_its_own_row(
    space: FakeSpace, client: TestClient
) -> None:
    _library(space)

    said = client.post("/v1/documents/draft/ungroup")

    assert said.status_code == 200 and said.json() == {"ungrouped": "draft", "pairs": 1}
    assert space.split == ["draft"]
    items = {i["name"]: i for i in client.get("/v1/documents").json()["items"]}
    assert set(items) == {_DRAFT.file, _FINAL.file, "memo.pdf"}
    assert client.post("/v1/documents/memo/ungroup").status_code == 404

"""Box as a place documents are read from, against a fake Box: folders listed a level at a time,
what was picked opened into the documents in it, a file's bytes fetched only while needed.
Invented folders and files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from graphme.adapters.sources.box import API, BoxSource, Picked, origin, web_url

TREE: dict[str, dict[str, Any]] = {
    "0": {"name": "All Files", "parents": [], "items": ["10", "f1", "f9"]},
    "10": {"name": "Specs", "parents": [("0", "All Files")], "items": ["11", "f2", "f3"]},
    "11": {"name": "Old", "parents": [("0", "All Files"), ("10", "Specs")], "items": ["f4"]},
}
FILES = {
    "f1": ("Leak Test Procedure.pdf", b"%PDF-1.4 a"),
    "f2": ("Valve Spec.docx", b"docx b"),
    "f3": ("notes.txt", b"not a document"),
    "f4": ("Price List.xlsx", b"xlsx c"),
    "f9": ("Meeting.pptx", b"pptx d"),
}


class _Box:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.gone: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "dl.boxcloud.test":  # the token never follows to it
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=FILES[request.url.path.strip("/")][1])
        self.tokens.append(request.headers["Authorization"])
        path = request.url.path.removeprefix("/2.0/")
        kind, _, rest = path.partition("/")
        item_id, _, tail = rest.partition("/")
        if item_id in self.gone:
            return httpx.Response(404, json={"code": "not_found"})
        if kind == "folders" and tail == "items":
            folder = TREE[item_id]
            return httpx.Response(200, json={"total_count": len(folder["items"]), "entries": [
                self._item(i) for i in folder["items"]]})
        if kind == "folders":
            folder = TREE[item_id]
            return httpx.Response(200, json={"id": item_id, "type": "folder",
                                             "name": folder["name"], "path_collection": {
                                                 "entries": [{"id": i, "name": n}
                                                             for i, n in folder["parents"]]}})
        if kind == "files" and tail == "content":  # Box answers with where the bytes are
            return httpx.Response(302, headers={
                "Location": f"https://dl.boxcloud.test/{item_id}"})
        if kind == "files":
            return httpx.Response(200, json=self._item(item_id))
        return httpx.Response(404)

    @staticmethod
    def _item(item_id: str) -> dict[str, Any]:
        if item_id in TREE:
            return {"id": item_id, "type": "folder", "name": TREE[item_id]["name"]}
        name, content = FILES[item_id]
        return {"id": item_id, "type": "file", "name": name, "size": len(content),
                "sha1": f"sha1-{item_id}", "file_version": {"id": f"v-{item_id}"}}


def _source(tmp_path: Path, box: _Box) -> BoxSource:
    return BoxSource(httpx.Client(transport=httpx.MockTransport(box)), lambda: "invented-token",
                     tmp_path / "copies")


def test_a_folder_is_listed_a_level_at_a_time_with_where_it_is(tmp_path: Path) -> None:
    box = _Box()
    listed = _source(tmp_path, box).folder("11")

    assert listed["name"] == "Old"
    assert [p["name"] for p in listed["path"]] == ["All Files", "Specs"]
    assert listed["items"] == [{"id": "f4", "type": "file", "name": "Price List.xlsx",
                                "size": len(FILES["f4"][1])}]
    assert set(box.tokens) == {"Bearer invented-token"}


def test_a_listing_shows_folders_and_documents_only(tmp_path: Path) -> None:
    listed = _source(tmp_path, _Box()).folder("10")

    assert [(i["type"], i["name"]) for i in listed["items"]] == [
        ("folder", "Old"), ("file", "Valve Spec.docx")]  # notes.txt is no document


def test_what_was_picked_opens_into_every_document_in_it(tmp_path: Path) -> None:
    box = _Box()
    box.gone.add("f9")
    found, lost = _source(tmp_path, box).documents(
        [Picked("folder", "10", "Specs"), Picked("file", "f1", "Leak Test Procedure.pdf"),
         Picked("file", "f9", "Meeting.pptx"), Picked("file", "f2", "Valve Spec.docx")])

    assert sorted(f.name for f in found) == [
        "Leak Test Procedure.pdf", "Price List.xlsx", "Valve Spec.docx"]  # each once
    assert lost == ["Meeting.pptx"]
    spec = next(f for f in found if f.id == "f2")
    assert spec.version == "v-f2" and spec.origin == origin("f2", "Valve Spec.docx")


def test_a_files_bytes_are_fetched_into_a_folder_of_their_own_and_removed_after(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, _Box())

    with source.open_copy(origin("f1", "Leak Test Procedure.pdf")) as copy:
        assert copy.read_bytes() == FILES["f1"][1] and copy.name == "Leak Test Procedure.pdf"
        held = copy.parent
    assert not held.exists()


def test_a_file_gone_from_box_is_not_found(tmp_path: Path) -> None:
    box = _Box()
    box.gone.add("f1")

    with pytest.raises(FileNotFoundError), _source(tmp_path, box).open_copy(
            origin("f1", "Leak Test Procedure.pdf")):
        pass


def test_a_box_file_opens_in_the_browser_at_box() -> None:
    assert web_url(origin("f1", "a.pdf")) == "https://app.box.com/file/f1"
    assert API.startswith("https://api.box.com/")

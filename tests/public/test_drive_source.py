"""Google Drive as the place originals are read from, against a fake Drive. Invented files."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from graphme.adapters.sources.drive import API, FOLDER, DriveSource, origin
from graphme.application.corpus_build import build_graph, origin_of, with_origin
from tests.fakes.documents import named_document

PDF = b"%PDF-1.4 invented report"


class FakeDrive:
    """Files and folders as the Drive API v3 describes them; `gone` are no longer readable."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {
            "folder-1": {"id": "folder-1", "name": "Reports", "mimeType": FOLDER},
            "file-pdf": {"id": "file-pdf", "name": "report.pdf", "mimeType": "application/pdf",
                         "sha256Checksum": hashlib.sha256(PDF).hexdigest(),
                         "headRevisionId": "rev-1", "parents": ["folder-1"]},
            "file-gdoc": {"id": "file-gdoc", "name": "notes",
                          "mimeType": "application/vnd.google-apps.document",
                          "parents": ["folder-1"]},
        }
        self.content = {"file-pdf": PDF}
        self.gone: set[str] = set()
        self.fetched: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer invented-access"
        path = request.url.path.removeprefix(httpx.URL(API).path).strip("/")
        if path == "files":
            folder = request.url.params["q"].split("'")[1]
            return httpx.Response(200, json={"files": [
                {"id": f["id"]} for f in self.files.values() if folder in f.get("parents", [])]})
        file_id = path.removeprefix("files/")
        if file_id in self.gone or file_id not in self.files:
            return httpx.Response(404)
        if request.url.params.get("alt") == "media":
            self.fetched.append(file_id)
            return httpx.Response(200, content=self.content[file_id])
        return httpx.Response(200, json=self.files[file_id])


def _source(drive: FakeDrive, tmp_path: Path) -> DriveSource:
    return DriveSource(httpx.Client(transport=httpx.MockTransport(drive)),
                       lambda: "invented-access", tmp_path / "copies")


def test_a_picked_folder_gives_its_documents_and_leaves_web_formats_out(tmp_path: Path) -> None:
    drive = FakeDrive()
    drive.gone.add("file-lost")

    found, lost = _source(drive, tmp_path).documents(["folder-1", "file-lost"])

    assert [f.name for f in found] == ["report.pdf"] and lost == ["file-lost"]
    assert found[0].origin == "gdrive://file-pdf/report.pdf"
    assert drive.fetched == []  # described, not fetched


def test_a_copy_lasts_while_it_is_needed_and_a_gone_file_says_so(tmp_path: Path) -> None:
    drive = FakeDrive()
    source = _source(drive, tmp_path)

    with source.open_copy(origin("file-pdf", "report.pdf")) as copy:
        assert copy.read_bytes() == PDF and copy.name == "report.pdf"
    assert not copy.exists() and not any((tmp_path / "copies").iterdir())
    drive.gone.add("file-pdf")
    with pytest.raises(FileNotFoundError), source.open_copy(origin("file-pdf", "report.pdf")):
        pass


def test_a_document_read_from_a_copy_is_recorded_as_its_original() -> None:
    read = named_document("a", "report.pdf")

    kept = with_origin(read, "gdrive://file-pdf/report.pdf",
                       {"drive_file_id": "file-pdf", "web_url": "https://drive.invented/x",
                        "drive_revision": None})

    assert origin_of(kept) == "gdrive://file-pdf/report.pdf"
    root = next(n for n in kept.nodes if n.id == "a")
    assert root.metadata["drive_file_id"] == "file-pdf" and "drive_revision" not in root.metadata


def test_a_drive_file_built_again_replaces_its_earlier_reading() -> None:
    from tests.public.test_rebuild import _Space

    space = _Space()
    first = with_origin(named_document("a", "report.pdf"), "gdrive://file-pdf/report.pdf", {})
    edited = with_origin(named_document("b", "report.pdf"), "gdrive://file-pdf/report.pdf", {})
    space.read_from = lambda source_path: {  # type: ignore[method-assign]
        d for d, doc in space.documents.items() if origin_of(doc) == source_path}

    build_graph([Path("report.pdf")], process=lambda _: first, space=space)  # type: ignore[arg-type]
    build_graph([Path("report.pdf")], process=lambda _: edited, space=space)  # type: ignore[arg-type]

    assert list(space.documents) == ["b"]

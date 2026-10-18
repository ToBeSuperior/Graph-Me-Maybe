"""Files the server holds: uploaded and kept, their pages, and what was read from them."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.server import app as server_app
from graphme.server.state import Demo
from tests.fakes.documents import named_document as _doc
from tests.public.server.conftest import FakeSpace


def test_an_upload_is_kept_and_nothing_is_read(demo: Demo, client: TestClient) -> None:
    """Model calls are spent only when a job asks: an upload starts no reading."""
    submitted: list[Any] = []
    demo.readers.submit = lambda *args: submitted.append(args)  # type: ignore[assignment,method-assign]

    said = client.post("/v1/files", files={"file": ("memo.docx", b"content")})

    assert said.status_code == 200 and said.json()["state"] == "idle"
    assert Path(demo.catalog[said.json()["id"]]).read_bytes() == b"content"
    assert submitted == [] and demo.sessions == {}


def test_an_uploaded_file_keeps_when_it_was_last_changed(
    demo: Demo, client: TestClient
) -> None:
    said = client.post("/v1/files", files={"file": ("memo.docx", b"content")},
                       data={"modified": "1700000000123"}).json()

    assert os.stat(demo.catalog[said["id"]]).st_mtime == 1_700_000_000.123


def test_an_upload_that_cannot_be_kept_is_turned_away(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for bad in ("nan", "inf", "-5", "1e30"):
        told = client.post("/v1/files", files={"file": ("memo.docx", b"content")},
                           data={"modified": bad})
        assert told.status_code == 400, bad
    assert client.post("/v1/files", files={"file": ("notes.txt", b"x")}).status_code == 400
    assert client.post("/v1/files", files={"file": ("memo.docx", b"")}).status_code == 400
    monkeypatch.setattr(server_app, "MAX_UPLOAD_BYTES", 4)
    assert client.post("/v1/files", files={"file": ("memo.docx", b"12345")}).status_code == 413
    assert client.post("/v1/files", files={"file": ("memo.docx", b"1234")}).status_code == 200


def test_an_uploaded_name_never_reaches_outside_the_servers_folder(
    demo: Demo, client: TestClient
) -> None:
    said = client.post("/v1/files", files={"file": ("../../outside.docx", b"content")}).json()

    kept = Path(demo.catalog[said["id"]])
    assert kept.is_relative_to(demo.root / "uploads") and kept.name == "outside.docx"


def test_a_file_the_space_holds_is_said_to_be_there(space: FakeSpace, client: TestClient) -> None:
    space.hashes.add(hashlib.sha256(b"already there").hexdigest())

    said = client.post("/v1/files", files={"file": ("memo.docx", b"already there")}).json()

    assert said["state"] == "in_space"


def test_a_file_outside_the_graph_is_taken_off_the_list_but_not_while_read(
    demo: Demo, client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "a.docx").write_bytes(b"x")
    doc = demo.open_path(str(tmp_path / "a.docx"))
    demo.session(doc).state = "reading"
    assert client.delete(f"/v1/files/{doc}").status_code == 409
    demo.session(doc).state = "error"
    assert client.delete(f"/v1/files/{doc}").json() == {"deleted": doc}
    assert doc not in demo.catalog and doc not in demo.sessions
    assert client.delete(f"/v1/files/{doc}").status_code == 404


def _read(demo: Demo, tmp_path: Path) -> str:
    """A file read as far as its pages and its graph, with no renderer and no model."""
    (tmp_path / "보고서.pdf").write_bytes(b"%PDF-1.4")
    doc = demo.open_path(str(tmp_path / "보고서.pdf"))
    session = demo.session(doc)
    session.extracted = _doc("a", "보고서.pdf")
    session.pages = (RenderedPage(index=1, width=20, height=10, png=b""),)
    session.page_of = {"a": 1}
    session.workspace.mkdir(parents=True)
    (session.workspace / "page-01.png").write_bytes(b"\x89PNG invented")
    session.state = "done"
    return doc


def test_a_files_pages_and_what_was_read_from_them(
    demo: Demo, client: TestClient, tmp_path: Path
) -> None:
    doc = _read(demo, tmp_path)

    pages = client.get(f"/v1/files/{doc}/pages").json()
    assert pages["source"] == "보고서.pdf" and pages["state"] == "done"
    assert [p["index"] for p in pages["sheets"]] == [1] and pages["pending"] is False
    assert client.get(f"/v1/files/{doc}/pages/1/image").content == b"\x89PNG invented"
    assert client.get(f"/v1/files/{doc}/pages/9/image").status_code == 404
    graph = client.get(f"/v1/files/{doc}/graph").json()
    assert graph["nodes"] and graph["pages"] == {"a": 1} and graph["crops"] == []
    told = client.get(f"/v1/files/{doc}/pages/1/crop", params={"node": "a"})
    assert told.status_code == 404, "no crop stored for it"
    assert client.get(f"/v1/files/{doc}/pages/1/crop", params={"node": "x"}).status_code == 404
    cypher = client.get(f"/v1/files/{doc}/graph.cypher")
    assert "UTF-8''%EB%B3%B4" in cypher.headers["content-disposition"]


def test_where_each_node_is_drawn_is_measured_once_per_graph(
    demo: Demo, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _read(demo, tmp_path)
    measured: list[str] = []

    def placed(extracted: Any, pages: Any, source: Path) -> dict[str, Any]:
        measured.append(source.name)
        return {"a": (1, (0, 0, 5, 5))}

    monkeypatch.setattr(server_app, "placed_units", placed)

    for _ in range(2):
        said = client.get(f"/v1/files/{doc}/boxes").json()
        assert said == {"boxes": {"a": {"page": 1, "box": [0, 0, 5, 5]}}}
    assert measured == ["보고서.pdf"]


def test_a_file_not_read_yet_or_not_held_is_said_so(
    demo: Demo, client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    doc = demo.open_path(str(tmp_path / "a.pdf"))

    assert client.get(f"/v1/files/{doc}/pages").json()["sheets"] == []
    assert client.get(f"/v1/files/{doc}/graph").status_code == 409
    for path in ("pages", "graph", "boxes", "graph.cypher", "pages/1/image"):
        assert client.get(f"/v1/files/nothing/{path}").status_code == 404, path

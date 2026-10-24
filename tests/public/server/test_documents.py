"""The documents in the graph space, the space as a whole, and its bundle download."""
from __future__ import annotations

import hashlib
import io
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from graphme.server import app as server_app
from graphme.server.state import Demo
from tests.public.server.conftest import FakeSpace


def test_one_list_holds_the_space_and_what_the_server_is_still_reading(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    """Every document in the graph space, matched to the server's own reading of it when
    there is one, then the server's files not in the space yet."""
    inside, outside = tmp_path / "spec.docx", tmp_path / "draft.pdf"
    inside.write_bytes(b"x")
    outside.write_bytes(b"%PDF-1.4")
    reading_inside, reading_outside = demo.open_path(str(inside)), demo.open_path(str(outside))
    demo.session(reading_outside)  # a file the server began on
    never = tmp_path / "never.xlsx"
    never.write_bytes(b"x")
    demo.open_path(str(never))
    space.documents = [
        {"id": "s1", "label": "spec.docx", "title": None, "mentions": 0,
         "source_path": str(inside), "modified_at": 1_600_000_000},
        {"id": "s2", "label": "old.docx", "title": None, "mentions": 1,
         "source_path": "/gone/old.docx"},
    ]

    said = client.get("/v1/documents").json()
    items = {i["name"]: i for i in said["items"]}

    assert said["space_ok"] is True
    assert items["spec.docx"] == {"name": "spec.docx", "format": "docx", "in_space": True,
                                  "document_id": "s1", "file_id": reading_inside,
                                  "state": "idle", "modified": 1_600_000_000, "web_url": None}
    assert items["old.docx"]["in_space"] and items["old.docx"]["file_id"] is None
    assert items["draft.pdf"] == {"name": "draft.pdf", "format": "pdf", "in_space": False,
                                  "document_id": None, "file_id": reading_outside,
                                  "state": "idle", "modified": outside.stat().st_mtime,
                                  "web_url": None}
    assert "never.xlsx" not in items, "a file nothing began on is not listed"
    names = [i["name"] for i in said["items"]]
    assert names.index("old.docx") < names.index("draft.pdf"), "the space's documents first"
    shown = client.get("/v1/graph/map").json()["documents"]
    assert shown and all("source_path" not in d for d in shown), "this machine's paths stay here"


def test_one_content_reached_by_two_paths_is_one_entry(
    demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    content = b"the same file"
    uploaded = demo.add("spec.docx", content)
    demo.session(uploaded)
    space.documents = [{"id": "s1", "label": "spec.docx", "title": None, "mentions": 0,
                        "source_path": "/elsewhere/folder/spec.docx",
                        "content_hash": hashlib.sha256(content).hexdigest(),
                        "modified_at": None}]

    items = client.get("/v1/documents").json()["items"]

    assert [(i["name"], i["in_space"], i["file_id"]) for i in items] == [
        ("spec.docx", True, uploaded)]


def test_the_list_still_shows_the_servers_files_when_the_space_is_down(
    demo: Demo, client: TestClient, tmp_path: Path
) -> None:
    def down(**_: object) -> None:
        raise ConnectionError("no Neo4j")

    demo.space = down  # type: ignore[assignment,method-assign]
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    demo.session(demo.open_path(str(tmp_path / "a.pdf")))

    said = client.get("/v1/documents").json()

    assert said["space_ok"] is False and [i["name"] for i in said["items"]] == ["a.pdf"]
    assert client.get("/v1/graph/map").status_code == 503


def test_the_map_draws_the_most_linked_documents_when_there_are_too_many(
    space: FakeSpace, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past MAP_LIMIT documents, the ones with the fewest links leave the map first, and
    with them every line to them; the caller is told how many there are in all."""
    monkeypatch.setattr(server_app, "MAP_LIMIT", 2)
    space.documents = [{"id": d, "label": f"{d}.docx", "title": None, "mentions": 0,
                        "source_path": None} for d in ("a", "b", "c", "d")]
    space.links = [{"from": "a", "to": "b", "count": 3, "matched_by": ["number"]},
                   {"from": "c", "to": "d", "count": 1, "matched_by": ["number"]}]
    space.similar = [{"from": "a", "to": "c", "score": 0.9}]

    shown = client.get("/v1/graph/map").json()

    # a: 3 + 1, b: 3, c: 1 + 1, d: 1
    assert [d["id"] for d in shown["documents"]] == ["a", "b"]
    assert shown["links"] == [{"from": "a", "to": "b", "count": 3, "matched_by": ["number"]}]
    assert shown["similar"] == []
    assert shown["total"] == 4


def test_a_document_is_deleted_with_the_servers_reading_of_it_but_not_while_a_build_runs(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    space.held["space-a"] = "a.docx"
    (tmp_path / "a.docx").write_bytes(b"x")
    doc = demo.open_path(str(tmp_path / "a.docx"))
    demo.session(doc).extracted = SimpleNamespace(document=SimpleNamespace(id="space-a"))  # type: ignore[assignment]

    demo.build.state = "reading"
    assert client.delete("/v1/documents/space-a").status_code == 409
    demo.build.state = "done"
    assert client.delete("/v1/documents/space-a").json() == {"deleted": "space-a"}
    assert space.deleted == [["space-a"]]
    assert doc not in demo.sessions and doc not in demo.catalog


def test_deleting_a_document_the_space_does_not_hold_is_a_404(
    space: FakeSpace, client: TestClient
) -> None:
    told = client.delete("/v1/documents/nope")
    assert told.status_code == 404 and "nope" in told.json()["detail"]
    assert space.deleted == []


def test_a_document_the_space_does_not_hold_has_no_graph(
    space: FakeSpace, client: TestClient
) -> None:
    told = client.get("/v1/documents/nope/graph")
    assert told.status_code == 404 and told.json()["detail"]
    space.held["d1"] = "spec.docx"  # an id of hex digits: the invented document hashes it
    found = client.get("/v1/documents/d1/graph").json()
    assert found["nodes"] and found["chunks"] == []


def test_a_documents_file_is_found_and_its_pages_drawn_again(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    """Read before a restart, a document is drawn again from the file the space remembers:
    rendering only, never a model."""
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4")
    space.paths["s1"] = str(source)
    drawn: list[str] = []
    demo._prepare = lambda session: drawn.append(session.source.name)  # type: ignore[method-assign]

    said = client.post("/v1/documents/s1/file")

    assert said.status_code == 200 and said.json() == {"file_id": demo.doc_id(str(source))}
    assert drawn == ["paper.pdf"]
    assert client.post("/v1/documents/gone/file").status_code == 404


def test_a_document_whose_pages_cannot_be_drawn_is_a_503(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4")
    space.paths["s1"] = str(source)

    def broken(session: object) -> None:
        raise RuntimeError(f"renderer failed on {source}")

    demo._prepare = broken  # type: ignore[method-assign]
    told = client.post("/v1/documents/s1/file")

    assert told.status_code == 503 and str(tmp_path) not in told.json()["detail"]


def test_the_graph_is_emptied_with_what_the_server_read_but_not_while_a_build_runs(
    demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    space.held.update({"a": "a.docx", "b": "b.docx"})
    doc = demo.add("memo.docx", b"content")
    demo.session(doc)

    assert client.post("/v1/graph/reset", json={}).status_code == 400, "asked to confirm"
    demo.build.state = "reading"
    assert client.post("/v1/graph/reset", json={"confirm": True}).status_code == 409
    demo.build.state = "done"
    assert client.post("/v1/graph/reset", json={"confirm": True}).json() == {"deleted": 2}
    assert demo.sessions == {} and demo.catalog == {} and demo.hashes == {}


def test_the_graph_is_read_back_whole_or_by_the_documents_named(
    space: FakeSpace, client: TestClient
) -> None:
    whole = client.get("/v1/graph").json()
    assert whole["documents"] == 1 and "chunks" not in whole
    assert client.get("/v1/graph?docs=a&docs=b").status_code == 200
    cypher = client.get("/v1/graph.cypher?docs=a")
    assert cypher.status_code == 200 and "attachment" in cypher.headers["content-disposition"]
    assert space.asked == [None, ["a", "b"], ["a"]]


def test_each_bundle_download_is_a_file_of_its_own_and_is_gone_once_sent(
    demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    """Two callers choosing different documents at once must not get each other's zip, and
    a download leaves nothing behind in the server's folder."""
    first = client.get("/v1/export/bundle?docs=a")
    second = client.get("/v1/export/bundle?docs=b")

    names = set(zipfile.ZipFile(io.BytesIO(first.content)).namelist())
    assert {"import.sh", "schema.cypher", "nodes_DocNode_Document.csv"} <= names
    assert zipfile.ZipFile(io.BytesIO(second.content)).namelist()
    assert space.asked == [["a"], ["b"]]
    assert not list(demo.root.rglob("*.zip"))


def test_a_bundle_that_fails_after_the_space_opened_is_a_500(
    demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    def broken(docs: object) -> None:
        raise RuntimeError("zip failed")

    demo.bundle_zip = broken  # type: ignore[method-assign,assignment]
    quiet = TestClient(client.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
    assert quiet.get("/v1/export/bundle").status_code == 500


def test_a_documents_file_with_the_space_down_is_a_503(
    demo: Demo, client: TestClient
) -> None:
    def down(**_: object) -> None:
        raise ConnectionError("no Neo4j")

    demo.space = down  # type: ignore[assignment,method-assign]
    assert client.post("/v1/documents/s1/file").status_code == 503


def test_a_file_opened_from_its_folder_and_uploaded_too_is_one_entry(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    """Opened by its path, then uploaded with its folder: two readings of one content on the
    server, one document in the list."""
    content = b"the same file"
    original = tmp_path / "folder" / "spec.docx"
    original.parent.mkdir()
    original.write_bytes(content)
    demo.session(demo.open_path(str(original)))
    demo.session(demo.add("spec.docx", content))
    space.documents = [{"id": "s1", "label": "spec.docx", "title": None, "mentions": 0,
                        "source_path": "/elsewhere/spec.docx",
                        "content_hash": hashlib.sha256(content).hexdigest(),
                        "modified_at": None}]

    items = client.get("/v1/documents").json()["items"]

    assert [(i["name"], i["in_space"]) for i in items] == [("spec.docx", True)]


def test_the_files_a_question_found_leave_in_one_zip_by_its_folders(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    found, linked, attached = tmp_path / "spec.docx", tmp_path / "other" / "spec.docx", (
        tmp_path / "memo.emb0123abcd.pdf")
    found.write_bytes(b"first")
    linked.parent.mkdir()
    linked.write_bytes(b"second")
    attached.write_bytes(b"%PDF-1.4")
    space.paths = {"d1": str(found), "d2": str(linked), "d3": str(attached),
                   "d4": str(tmp_path / "gone.xlsx")}
    space.held = {"d1": "spec.docx", "d2": "spec.docx", "d3": "memo.pdf", "d4": "gone.xlsx"}

    got = client.get("/v1/documents/files.zip", params=[
        ("doc", "d1"), ("folder", "1 found"), ("doc", "d2"), ("folder", "1 found"),
        ("doc", "d3"), ("folder", "../2 linked"), ("doc", "d4"), ("folder", "2 linked"),
        ("doc", "nope"), ("folder", "2 linked"), ("name", "a question?")])

    assert got.status_code == 200, got.text
    assert "attachment" in got.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
        names = archive.namelist()
        assert names[:3] == ["1 found/spec.docx", "1 found/spec (2).docx", "2 linked/memo.pdf"]
        assert archive.read("1 found/spec (2).docx") == b"second"
        # Nothing the server could not open is passed over in silence.
        missing = archive.read("missing-files.txt").decode("utf-8")
    # Each named as a reader can find it: the folder it was for, its name, where it was looked
    # for -- and an id the graph does not hold, said to be one.
    lines = missing.splitlines()
    assert any(line.startswith("2 linked/gone.xlsx") and str(tmp_path / "gone.xlsx") in line
               for line in lines), missing
    assert any(line.startswith("nope") and "not in the graph" in line for line in lines), missing
    assert not any(".." in n or n.startswith("/") for n in names)


def test_a_zip_of_nothing_chosen_is_refused(client: TestClient) -> None:
    assert client.get("/v1/documents/files.zip").status_code in (400, 422)


def test_a_zips_folder_and_name_carry_no_control_or_turning_marks(
    space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    # A folder word or a question can hold what turns text around on screen ("txt.exe" read as
    # "exe.txt") or a control character; a name written into a zip holds neither.
    found = tmp_path / "spec.docx"
    found.write_bytes(b"first")
    space.paths, space.held = {"d1": str(found)}, {"d1": "spec.docx"}

    got = client.get("/v1/documents/files.zip", params=[
        ("doc", "d1"), ("folder", "‮1 found\x07"), ("name", "why‮?\x01")])

    assert got.status_code == 200, got.text
    assert "‮" not in got.headers["content-disposition"]
    assert "%E2%80%AE" not in got.headers["content-disposition"].upper()
    with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
        assert archive.namelist() == ["1 found/spec.docx"]


def test_a_document_read_and_waiting_for_its_turn_in_a_build_is_not_outside(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path
) -> None:
    """A build reads its documents side by side and puts each once it is read: one read and
    not put yet is waiting to be put, not left out of the graph."""
    waiting, left = tmp_path / "waiting.pdf", tmp_path / "left.pdf"
    for path in (waiting, left):
        path.write_bytes(b"%PDF-1.4")
    queued, read_alone = demo.open_path(str(waiting)), demo.open_path(str(left))
    for doc in (queued, read_alone):
        demo.session(doc).state = "done"
    demo.putting = {queued}  # read, not put yet

    items = {i["name"]: i["state"] for i in client.get("/v1/documents").json()["items"]}

    assert items == {"waiting.pdf": "queued", "left.pdf": "done"}


def test_a_document_being_read_says_how_far_its_reading_is(
    demo: Demo, client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF-1.4")
    doc = demo.open_path(str(source))
    session = demo.session(doc)
    session.state = "reading"
    session.progress.estimate(figures=2, outline=4)
    session.progress.plan("figures", 2)
    session.progress.step("figures", 2)
    session.progress.plan("outline", 4)
    session.progress.step("outline")

    said = client.get(f"/v1/files/{doc}/pages").json()["progress"]

    assert said == {"stage": "outline", "done": 1, "total": 4, "percent": 50}


def test_a_document_says_how_long_its_reading_took(
    demo: Demo, client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "memo.pdf"
    source.write_bytes(b"%PDF-1.4")
    doc = demo.open_path(str(source))
    session = demo.session(doc)
    assert client.get(f"/v1/files/{doc}/pages").json()["elapsed"] is None  # not read yet

    session.state, session.started_at = "reading", time.monotonic() - 65
    assert client.get(f"/v1/files/{doc}/pages").json()["elapsed"] >= 65

    session.state, session.ended_at = "done", session.started_at + 90
    assert client.get(f"/v1/files/{doc}/pages").json()["elapsed"] == 90

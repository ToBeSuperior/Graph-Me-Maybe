"""Jobs over HTTP: started with 202 and an id, polled, cancelled, and their files fetched."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.server.state import Demo
from tests.public.server.conftest import FakeAdmin, FakeSpace, read_as, until


def _upload(client: TestClient, name: str = "a.docx") -> str:
    said: str = client.post("/v1/files", files={"file": (name, name.encode())}).json()["id"]
    return said


def _settled(client: TestClient, job_id: str) -> dict[str, Any]:
    said: dict[str, Any] = {}

    def done() -> bool:
        said.update(client.get(f"/v1/jobs/{job_id}").json())
        return said["state"] in ("done", "error", "cancelled")

    until(done)
    return said


def test_a_build_is_a_job_polled_by_its_id(
    demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    file_id = _upload(client)

    started = client.post("/v1/jobs/build", json={"files": [file_id]})

    assert started.status_code == 202
    job = started.json()
    assert job["kind"] == "build" and started.headers["location"] == f"/v1/jobs/{job['id']}"
    assert _settled(client, job["id"])["state"] == "done"
    assert space.put == ["a"]
    assert [j["id"] for j in client.get("/v1/jobs").json()] == [job["id"]]


def test_a_folder_on_the_servers_machine_is_built(
    demo: Demo, space: FakeSpace, client: TestClient, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named as the server's working directory sees it, and kept by its absolute path."""
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.docx").write_bytes(b"a")
    (folder / "notes.txt").write_text("not a document")
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    monkeypatch.chdir(tmp_path)

    job = client.post("/v1/jobs/build", json={"folder": "docs"}).json()

    assert _settled(client, job["id"])["state"] == "done" and space.put == ["a"]
    assert job["source"] == str(folder)
    assert all(Path(path).is_absolute() for path in demo.catalog.values())
    assert client.post("/v1/jobs/build", json={"folder": str(tmp_path / "x")}).status_code == 400
    assert client.post("/v1/jobs/build", json={"folder": "a\x00b"}).status_code == 400
    assert not any(path.endswith(".txt") for path in demo.catalog.values())
    assert client.post("/v1/jobs/build", json={"files": ["unknown"]}).status_code == 400
    assert client.post("/v1/jobs/build", json={"files": "not a list"}).status_code == 400


def test_a_read_job_puts_nothing_in_the_space(
    demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    file_id = _upload(client)

    job = client.post("/v1/jobs/read", json={"files": [file_id]}).json()

    assert job["kind"] == "read" and _settled(client, job["id"])["state"] == "done"
    assert space.put == []
    assert client.post("/v1/jobs/read", json={"files": []}).status_code == 400


def test_one_job_at_a_time_in_each_slot_and_the_slots_side_by_side(
    demo: Demo, client: TestClient
) -> None:
    release = threading.Event()

    def run(session: Any) -> None:
        release.wait(5)
        read_as("a", "a.docx")(session)

    demo._run = run  # type: ignore[method-assign]
    file_id = _upload(client)

    building = client.post("/v1/jobs/build", json={"files": [file_id]}).json()
    for second in ("/v1/jobs/build", "/v1/jobs/read"):
        told = client.post(second, json={"files": [file_id]})
        assert told.status_code == 409 and told.json()["detail"], second
    assert client.post("/v1/jobs/embed").status_code == 409
    dumping = client.post("/v1/jobs/dump", json={"docs": ["a"]})
    assert dumping.status_code == 202, "an export reads the graph; it runs beside a build"
    release.set()
    assert _settled(client, building["id"])["state"] == "done"
    assert _settled(client, dumping.json()["id"])["state"] == "done"


def test_a_build_is_cancelled_and_an_embedding_is_not(
    demo: Demo, client: TestClient
) -> None:
    release = threading.Event()
    demo._run = lambda session: release.wait(5)  # type: ignore[method-assign]
    job = client.post("/v1/jobs/build", json={"files": [_upload(client)]}).json()

    said = client.post(f"/v1/jobs/{job['id']}/cancel")
    release.set()

    assert said.status_code == 200 and said.json()["id"] == job["id"]
    assert _settled(client, job["id"])["state"] == "cancelled"
    assert client.post("/v1/jobs/nothing/cancel").status_code == 404

    class Rag:
        def embed(self, *, on_progress: Any = None) -> int:
            release.wait(5)
            return 0

    release.clear()
    demo.graph._rag = Rag()  # type: ignore[assignment]
    embedding = client.post("/v1/jobs/embed").json()
    assert embedding["kind"] == "embed"
    assert client.post(f"/v1/jobs/{embedding['id']}/cancel").status_code == 409
    release.set()
    assert _settled(client, embedding["id"])["state"] == "done"


def test_a_dump_is_made_in_the_background_and_then_fetched(
    space: FakeSpace, client: TestClient
) -> None:
    job = client.post("/v1/jobs/dump", json={"docs": ["a"]}).json()

    assert job["kind"] == "dump"
    settled = _settled(client, job["id"])
    assert settled["state"] == "done" and settled["dump_ready"] is True
    assert space.asked == [["a"]]
    got = client.get(f"/v1/jobs/{job['id']}/file")
    assert got.status_code == 200 and got.content == b"DUMP"

    later = client.post("/v1/jobs/dump").json()
    assert _settled(client, later["id"])["state"] == "done"
    assert client.get(f"/v1/jobs/{job['id']}/file").status_code == 404, "written over since"
    assert client.get(f"/v1/jobs/{later['id']}/file").status_code == 200
    assert client.post("/v1/jobs/dump", json={"docs": []}).status_code == 400


def test_an_upload_goes_only_to_an_aura_address_and_only_when_confirmed(
    space: FakeSpace, admin: FakeAdmin, client: TestClient
) -> None:
    good = {"uri": "neo4j+s://abcd1234.databases.neo4j.io", "user": "neo4j",
            "password": "s3cret-pw", "confirm": True, "docs": ["a"]}

    for bad in ({"uri": "bolt://x"}, {"confirm": False}, {"password": ""}, {"docs": []}):
        assert client.post("/v1/jobs/aura-upload", json={**good, **bad}).status_code == 400
    assert admin.uploads == []

    job = client.post("/v1/jobs/aura-upload", json=good).json()
    settled = _settled(client, job["id"])

    assert settled["kind"] == "aura-upload" and settled["state"] == "done"
    assert admin.uploads == [{"dump": b"DUMP", "uri": good["uri"], "user": "neo4j",
                              "password": "s3cret-pw"}]
    assert "s3cret-pw" not in str(settled) and "s3cret-pw" not in client.get("/v1/jobs").text
    assert space.asked == [["a"]]
    assert client.get(f"/v1/jobs/{job['id']}/file").status_code == 404, "not a dump job"


def test_jobs_are_listed_newest_first_and_an_unknown_one_is_a_404(
    demo: Demo, client: TestClient
) -> None:
    first = client.post("/v1/jobs/dump").json()
    _settled(client, first["id"])
    second = client.post("/v1/jobs/dump").json()
    _settled(client, second["id"])

    assert [j["id"] for j in client.get("/v1/jobs").json()] == [second["id"], first["id"]]
    assert client.get("/v1/jobs/nothing").status_code == 404
    assert client.get("/v1/jobs/nothing/file").status_code == 404


def test_a_build_stopped_passes_over_what_is_not_read_yet(
    demo: Demo, client: TestClient
) -> None:
    """Cancelled, the files still waiting are passed over -- not counted as failures."""
    release = threading.Event()
    demo._run = lambda session: release.wait(5)  # type: ignore[method-assign]
    files = [_upload(client, "a.docx"), _upload(client, "b.docx")]
    job = client.post("/v1/jobs/build", json={"files": files}).json()

    client.post(f"/v1/jobs/{job['id']}/cancel")
    release.set()
    said = _settled(client, job["id"])

    assert said["state"] == "cancelled" and said["failed"] == [] and said["skipped"] == 2

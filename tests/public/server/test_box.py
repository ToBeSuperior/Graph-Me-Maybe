"""Connecting Box from the app, against a fake Box: sign in, browse, pick, build. Invented
folders, files and values."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from graphme import processes
from graphme.adapters.sources.box_account import REVOKE, TOKEN
from graphme.adapters.sources.google import FileVault
from graphme.server import create_app, state
from graphme.server.state import Demo
from tests.public.server.conftest import FakeSpace, read_as, until
from tests.public.test_box_source import FILES, _Box

BASE = "http://127.0.0.1:8765"


class _Everything(_Box):
    """Box's sign-in endpoints and its API, with what was fetched counted."""

    def __init__(self) -> None:
        super().__init__()
        self.revoked: list[str] = []
        self.fetched: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == REVOKE:
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if str(request.url) == TOKEN:
            return httpx.Response(200, json={"access_token": "invented-access",
                                              "expires_in": 3600,
                                              "refresh_token": "invented-refresh"})
        if request.url.path.endswith("/content"):
            self.fetched.append(request.url.path.split("/")[-2])
        return super().__call__(request)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(processes, "open_in_browser", seen.append)
    # Never the machine's own credential store from a test.
    monkeypatch.setattr(state, "vault", lambda fallback, account="": FileVault(fallback))
    return seen


def _configured(demo: Demo, box: _Everything) -> TestClient:
    demo.settings = demo.settings.model_copy(update={
        "box_client_id": "invented-box-client", "box_client_secret": "invented-box-secret"})
    demo.client = httpx.Client(transport=httpx.MockTransport(box))
    return TestClient(create_app(demo), base_url=BASE)


def _connected(demo: Demo, box: _Everything) -> TestClient:
    client = _configured(demo, box)
    url = client.post("/v1/cloud/box/connect").json()["url"]
    back = client.get("/v1/cloud/box/callback", params={
        "state": parse_qs(urlsplit(url).query)["state"][0], "code": "invented-code"})
    assert "connected" in back.text
    return client


def test_without_a_box_app_box_says_it_is_not_set_up(demo: Demo, opened: list[str]) -> None:
    client = TestClient(create_app(demo), base_url=BASE)

    assert client.get("/v1/cloud/box").json() == {
        "configured": False, "connected": False, "picked": []}
    assert client.post("/v1/cloud/box/connect").status_code == 404
    assert opened == []


def test_connecting_opens_box_in_the_browser_and_its_answer_signs_in(
    demo: Demo, opened: list[str]
) -> None:
    client = _configured(demo, _Everything())

    url = client.post("/v1/cloud/box/connect").json()["url"]
    asked = parse_qs(urlsplit(url).query)
    assert opened == [url] and asked["redirect_uri"] == [f"{BASE}/v1/cloud/box/callback"]
    client.get("/v1/cloud/box/callback", params={"state": asked["state"][0],
                                                  "code": "invented-code"})

    assert client.get("/v1/cloud/box").json() == {
        "configured": True, "connected": True, "picked": []}


def test_box_refusing_says_why_and_connects_nothing(demo: Demo, opened: list[str]) -> None:
    client = _configured(demo, _Everything())
    url = client.post("/v1/cloud/box/connect").json()["url"]

    back = client.get("/v1/cloud/box/callback", params={
        "state": parse_qs(urlsplit(url).query)["state"][0], "error": "access_denied"})

    assert "access_denied" in back.text and "admin" in back.text
    assert client.get("/v1/cloud/box").json()["connected"] is False


def test_folders_are_browsed_and_what_is_picked_is_marked(
    demo: Demo, opened: list[str]
) -> None:
    client = _connected(demo, _Everything())

    top = client.get("/v1/cloud/box/folders/0").json()
    picked = client.put("/v1/cloud/box/picked", json={"items": [
        {"type": "folder", "id": "10", "name": "Specs"}]}).json()
    again = client.get("/v1/cloud/box/folders/0").json()

    assert [i["name"] for i in top["items"]] == ["Specs", "Leak Test Procedure.pdf",
                                                  "Meeting.pptx"]
    assert picked["picked"] == [{"type": "folder", "id": "10", "name": "Specs"}]
    assert [i["picked"] for i in again["items"]] == [True, False, False]


def test_a_build_reads_what_was_picked_and_passes_over_what_it_read_before(
    demo: Demo, space: FakeSpace, opened: list[str]
) -> None:
    box = _Everything()
    client = _connected(demo, box)
    demo._run = read_as("a", "Leak Test Procedure.pdf")  # type: ignore[method-assign]
    client.put("/v1/cloud/box/picked", json={"items": [
        {"type": "file", "id": "f1", "name": "Leak Test Procedure.pdf"}]})

    first = client.post("/v1/cloud/box/build").json()
    assert until(lambda: client.get(f"/v1/jobs/{first['id']}").json()["state"] == "done")
    space.hashes.add(hashlib.sha256(FILES["f1"][1]).hexdigest())  # the space holds it now
    second = client.post("/v1/cloud/box/build").json()
    assert until(lambda: client.get(f"/v1/jobs/{second['id']}").json()["state"] == "done")

    assert box.fetched == ["f1"]  # the same version is not fetched again
    assert client.get(f"/v1/jobs/{second['id']}").json()["known"] == 1
    assert space.put == ["a"]


def test_a_document_read_from_box_opens_at_box(demo: Demo, opened: list[str]) -> None:
    client = _connected(demo, _Everything())
    assert demo.sources.web_url("box://f1/Leak Test Procedure.pdf") == \
        "https://app.box.com/file/f1"
    assert client.get("/v1/cloud/box").status_code == 200


def test_disconnecting_revokes_and_forgets_what_was_picked(
    demo: Demo, opened: list[str]
) -> None:
    box = _Everything()
    client = _connected(demo, box)
    client.put("/v1/cloud/box/picked", json={"items": [
        {"type": "file", "id": "f1", "name": "Leak Test Procedure.pdf"}]})

    after: dict[str, Any] = client.delete("/v1/cloud/box").json()

    assert box.revoked == ["invented-refresh"]
    assert after == {"configured": True, "connected": False, "picked": []}


def test_another_sites_page_cannot_connect_or_build(demo: Demo, opened: list[str]) -> None:
    client = _configured(demo, _Everything())
    elsewhere = {"origin": "https://elsewhere.invented"}

    assert client.post("/v1/cloud/box/connect", headers=elsewhere).status_code == 403
    assert client.post("/v1/cloud/box/build", headers=elsewhere).status_code == 403
    assert opened == []


def _box_drive(tmp_path: Any) -> Any:
    root = tmp_path / "Box"
    (root / "Specs").mkdir(parents=True)
    (root / "Specs" / "Valve Spec.docx").write_bytes(b"docx")
    (root / "Leak Test Procedure.pdf").write_bytes(b"%PDF-1.4")
    return root


def test_box_drive_on_this_machine_is_found_and_browsed(demo: Demo, tmp_path: Any) -> None:
    root = _box_drive(tmp_path)
    demo.box_drive = lambda: root  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    found = client.get("/v1/box-drive").json()
    specs = client.get("/v1/box-drive/folders", params={"path": "Specs"}).json()

    assert found == {"found": True, "root": str(root)}
    assert [i["name"] for i in specs["items"]] == ["Valve Spec.docx"]
    assert client.get("/v1/box-drive/folders", params={"path": ".."}).status_code == 400


def test_without_box_drive_the_page_is_told_so(demo: Demo) -> None:
    demo.box_drive = lambda: None  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    assert client.get("/v1/box-drive").json() == {"found": False, "root": None}
    assert client.get("/v1/box-drive/folders", params={"path": ""}).status_code == 404


def test_what_is_checked_in_box_drive_is_built_together(
    demo: Demo, space: FakeSpace, tmp_path: Any
) -> None:
    root = _box_drive(tmp_path)
    demo.box_drive = lambda: root  # type: ignore[method-assign]
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    started = client.post("/v1/jobs/build", json={"paths": [
        str(root / "Specs"), str(root / "Leak Test Procedure.pdf")]})

    assert started.status_code == 202
    assert until(lambda: client.get(f"/v1/jobs/{started.json()['id']}").json()["state"] == "done")
    assert sorted(Path(p).name for p in demo.catalog.values()) == [
        "Leak Test Procedure.pdf", "Valve Spec.docx"]
    assert client.post("/v1/jobs/build", json={"paths": [str(root / "nope")]}).status_code == 400


def test_a_folder_slow_to_list_is_looked_through_behind_not_in_the_request(
    demo: Demo, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Box Drive lists a folder by asking Box: a large one takes minutes, and a build that walked
    # it inside the request answered nothing until it was done.
    import threading
    import time

    from graphme.server import app as server_app

    root = _box_drive(tmp_path)
    gate = threading.Event()
    walk = server_app.documents_at

    def slow(path: Any) -> Any:
        gate.wait(5)
        return walk(path)

    monkeypatch.setattr(server_app, "documents_at", slow)
    demo._run = read_as("a", "a.docx")  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    began = time.monotonic()
    started = client.post("/v1/jobs/build", json={"paths": [str(root / "Specs")]})
    answered = time.monotonic() - began
    gate.set()

    assert started.status_code == 202 and answered < 2
    assert started.json()["state"] == "reading" and started.json()["total"] == 0
    assert until(lambda: client.get(f"/v1/jobs/{started.json()['id']}").json()["state"] == "done")


def test_a_folder_with_nothing_to_read_says_so_on_the_build(
    demo: Demo, tmp_path: Any
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    client = TestClient(create_app(demo), base_url=BASE)

    started = client.post("/v1/jobs/build", json={"paths": [str(empty)]})

    assert started.status_code == 202
    assert until(lambda: client.get(f"/v1/jobs/{started.json()['id']}").json()["state"] == "error")
    assert "no readable document" in client.get(f"/v1/jobs/{started.json()['id']}").json()["error"]

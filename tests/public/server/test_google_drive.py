"""Connecting Google Drive from the app, against a fake Google. Invented values."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from graphme import processes
from graphme.adapters.sources.google import REVOKE, FileVault
from graphme.server import create_app, state
from graphme.server.state import Demo

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(processes, "open_in_browser", seen.append)
    # Never the machine's own credential store from a test.
    monkeypatch.setattr(state, "vault", lambda fallback: FileVault(fallback))
    return seen


def _google(revoked: list[bool]) -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        if str(request.url) == REVOKE:
            revoked.append(True)
            return httpx.Response(200)
        return httpx.Response(200, json={"access_token": "invented-access", "expires_in": 3600,
                                          "refresh_token": "invented-refresh"})
    return httpx.Client(transport=httpx.MockTransport(answer))


def _configured(demo: Demo, revoked: list[bool]) -> TestClient:
    demo.settings = demo.settings.model_copy(update={
        "google_client_id": "invented.apps.googleusercontent.com",
        "google_client_secret": "invented-secret"})
    demo.client = _google(revoked)
    return TestClient(create_app(demo), base_url=BASE)


def test_without_an_oauth_client_drive_says_it_is_not_set_up(
    demo: Demo, opened: list[str]
) -> None:
    client = TestClient(create_app(demo), base_url=BASE)

    assert client.get("/v1/cloud/google").json() == {
        "configured": False, "connected": False, "picked": 0}
    assert client.post("/v1/cloud/google/connect").status_code == 404
    assert opened == []


def test_connecting_opens_google_and_its_answer_keeps_the_files_picked(
    demo: Demo, opened: list[str]
) -> None:
    revoked: list[bool] = []
    client = _configured(demo, revoked)

    url = client.post("/v1/cloud/google/connect").json()["url"]
    asked = parse_qs(urlsplit(url).query)
    assert opened == [url]
    assert asked["redirect_uri"] == [f"{BASE}/v1/cloud/google/callback"]

    back = client.get("/v1/cloud/google/callback", params={
        "state": asked["state"][0], "code": "invented-code",
        "picked_file_ids": "file-a,file-b"})

    assert back.status_code == 200 and "connected" in back.text
    assert client.get("/v1/cloud/google").json() == {
        "configured": True, "connected": True, "picked": 2}


def test_an_answer_this_app_did_not_start_connects_nothing(
    demo: Demo, opened: list[str]
) -> None:
    client = _configured(demo, [])
    client.post("/v1/cloud/google/connect")

    back = client.get("/v1/cloud/google/callback", params={
        "state": "someone-elses", "code": "invented-code", "picked_file_ids": "file-a"})

    assert "not connected" in back.text
    assert client.get("/v1/cloud/google").json()["connected"] is False


def test_disconnecting_revokes_and_forgets_the_files_picked(
    demo: Demo, opened: list[str]
) -> None:
    revoked: list[bool] = []
    client = _configured(demo, revoked)
    url = client.post("/v1/cloud/google/connect").json()["url"]
    client.get("/v1/cloud/google/callback", params={
        "state": parse_qs(urlsplit(url).query)["state"][0], "code": "invented-code",
        "picked_file_ids": "file-a"})

    after: dict[str, Any] = client.delete("/v1/cloud/google").json()

    assert revoked == [True] and after == {"configured": True, "connected": False, "picked": 0}


def test_another_sites_page_cannot_connect(demo: Demo, opened: list[str]) -> None:
    client = _configured(demo, [])

    refused = client.post("/v1/cloud/google/connect",
                          headers={"origin": "https://elsewhere.invented"})

    assert refused.status_code == 403 and opened == []


def test_the_token_is_kept_where_only_its_owner_reads_it(
    demo: Demo, opened: list[str], tmp_path: Path
) -> None:
    client = _configured(demo, [])
    url = client.post("/v1/cloud/google/connect").json()["url"]
    client.get("/v1/cloud/google/callback", params={
        "state": parse_qs(urlsplit(url).query)["state"][0], "code": "invented-code"})

    kept = demo.root / "google.token"
    assert kept.read_text() == "invented-refresh" and kept.stat().st_mode & 0o077 == 0
    assert "invented-refresh" not in client.get("/v1/settings").text


def _signed_in(demo: Demo, drive: Any) -> None:
    """A Google account signed in already, with Drive answered by `drive`."""
    from graphme.adapters.sources.google import TOKEN

    def answer(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN:
            return httpx.Response(200, json={"access_token": "invented-access",
                                              "expires_in": 3600})
        return drive(request)

    demo.settings = demo.settings.model_copy(update={
        "google_client_id": "invented.apps.googleusercontent.com"})
    demo.client = httpx.Client(transport=httpx.MockTransport(answer))
    FileVault(demo.root / "google.token").put("invented-refresh")


def test_a_drive_build_fetches_only_what_the_space_does_not_hold(
    demo: Demo, space: Any, opened: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.public.test_drive_source import PDF, FakeDrive

    drive = FakeDrive()
    drive.files["file-held"] = {**drive.files["file-pdf"], "id": "file-held",
                                "name": "held.pdf", "sha256Checksum": "a" * 64}
    _signed_in(demo, drive)
    demo.google_picked(state.Picked(file_ids=("folder-1", "file-held")))
    space.hashes.add("a" * 64)  # the space holds that content already
    built: list[list[str]] = []

    def build_corpus(docs: list[str], source: str, **_: Any) -> Any:
        built.append(docs)
        assert [Path(demo.catalog[d]).read_bytes() for d in docs] == [PDF]
        return state.CorpusBuild(id="b", kind="build", state="reading", source=source)

    monkeypatch.setattr(demo, "build_corpus", build_corpus)
    build = demo.build_google()

    assert drive.fetched == ["file-pdf"] and build.known == 1
    [doc] = built[0]
    assert demo.origins[doc][0] == "gdrive://file-pdf/report.pdf"
    assert demo.session(doc).origin == "gdrive://file-pdf/report.pdf"


def test_a_drive_documents_original_is_fetched_when_a_page_needs_it(
    demo: Demo, opened: list[str]
) -> None:
    from tests.public.test_drive_source import PDF, FakeDrive

    drive = FakeDrive()
    _signed_in(demo, drive)

    with demo.sources.open_copy("gdrive://file-pdf/report.pdf") as copy:
        assert copy.read_bytes() == PDF
    assert not copy.exists()
    assert demo.sources.web_url("gdrive://file-pdf/report.pdf") == (
        "https://drive.google.com/file/d/file-pdf/view")


def test_the_copies_fetched_for_a_build_are_removed_after_it(
    demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = demo.root / "drive" / "file-pdf"
    folder.mkdir(parents=True)
    (folder / "report.pdf").write_bytes(b"%PDF-1.4")
    doc = demo.open_path(str(folder / "report.pdf"))
    demo.origins[doc] = ("gdrive://file-pdf/report.pdf", {})
    monkeypatch.setattr(demo, "_build_corpus", lambda docs, put, embed: None)

    demo._build_then_let_go([doc], True, True)

    assert not folder.exists()

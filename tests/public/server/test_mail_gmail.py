"""Gmail through the server: signed in in the browser, counted before anything is read, read
into a workspace of its own like an IMAP account, the sign-in revoked on disconnect. A fake
Google and Gmail; invented mail."""
# ruff: noqa: F811 -- the workspace fixtures are test_workspaces's, used by name
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from graphme.adapters.sources.google_sign_in import REVOKE, TOKEN, GoogleSignIn
from graphme.adapters.sources.sign_in import Client, FileVault
from graphme.server import create_app
from graphme.server.mail_accounts import MailAccounts
from graphme.server.state import Demo
from tests.public.server.conftest import read_as, until
from tests.public.server.test_workspaces import BASE, home, workspace_demo  # noqa: F401
from tests.public.test_gmail_mail import _Gmail

REFRESH = "invented-refresh-token"


class _Google(_Gmail):
    """Google's token and revoke endpoints in front of the fake Gmail."""

    def __init__(self) -> None:
        super().__init__()
        self.revoked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN:
            return httpx.Response(200, json={"access_token": "invented-access",
                                             "expires_in": 3600, "refresh_token": REFRESH})
        if str(request.url).startswith(REVOKE):
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        return super().__call__(request)


@pytest.fixture
def google() -> _Google:
    return _Google()


@pytest.fixture
def gmail(workspace_demo: Demo, google: _Google, home: Path,
          monkeypatch: pytest.MonkeyPatch) -> TestClient:
    http = httpx.Client(transport=httpx.MockTransport(google))
    sign_in = GoogleSignIn(Client(id="invented-client", secret="invented-secret"), http)
    workspace_demo._mail = MailAccounts(  # type: ignore[attr-defined]
        home, workspace_demo.workspace_registry(), lambda fallback, account: FileVault(fallback),
        google=sign_in, http=http)
    monkeypatch.setattr(workspace_demo, "google_sign_in", lambda: sign_in)
    monkeypatch.setattr("graphme.processes.open_in_browser", lambda url: None)
    workspace_demo._run = read_as("a", "m.eml")  # type: ignore[method-assign]
    return TestClient(create_app(workspace_demo), base_url=BASE)


def _signed_in(gmail: TestClient) -> dict[str, object]:
    url = gmail.post("/v1/mail/google/connect").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    page = gmail.get("/v1/mail/google/callback", params={"state": state, "code": "invented"})
    assert "kim@invented.test is connected" in page.text
    [account] = gmail.get("/v1/mail/accounts").json()
    return dict(account)


def test_signing_in_makes_a_mail_workspace_and_counts_without_reading(
    gmail: TestClient, google: _Google, home: Path, tmp_path: Path
) -> None:
    account = _signed_in(gmail)

    assert account["kind"] == "gmail" and account["address"] == "kim@invented.test"
    assert account["state"] == "counted"
    assert account["count"] == {"messages": 3, "attachments": 1, "attachment_bytes": None,
                                "oldest": "2026-01-01"}
    assert google.fetched == []
    assert {"name": "kim@invented.test", "kind": "mail"}.items() <= next(
        i for i in gmail.get("/v1/workspaces").json()["items"]
        if i["id"] == account["workspace"]).items()
    # The refresh token is kept in the vault alone: never answered, never in another file.
    assert REFRESH not in gmail.get("/v1/mail/accounts").text
    assert [p for p in tmp_path.rglob("*") if p.is_file() and REFRESH.encode() in p.read_bytes()
            ] == [home / ".graphme" / "mail" / str(account["id"])]


def test_reading_fetches_the_mail_into_the_accounts_workspace(
    gmail: TestClient, google: _Google, home: Path
) -> None:
    account = _signed_in(gmail)

    job = gmail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()

    assert until(lambda: gmail.get(f"/v1/jobs/{job['id']}").json()["state"] == "done")
    assert len(google.fetched) == 3
    assert gmail.get("/v1/mail/accounts").json()[0]["state"] == "current"


def test_an_empty_mailbox_is_read_without_an_error(gmail: TestClient, google: _Google) -> None:
    # A new account holds no mail yet: nothing to read is not a failure, as an empty folder is.
    google.mail.clear()
    account = _signed_in_empty(gmail)

    job = gmail.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()

    assert until(lambda: gmail.get(f"/v1/jobs/{job['id']}").json()["state"] != "reading")
    ended = gmail.get(f"/v1/jobs/{job['id']}").json()
    assert ended["state"] == "done" and ended["error"] is None and ended["documents"] == 0


def _signed_in_empty(gmail: TestClient) -> dict[str, object]:
    url = gmail.post("/v1/mail/google/connect").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    gmail.get("/v1/mail/google/callback", params={"state": state, "code": "invented"})
    [account] = gmail.get("/v1/mail/accounts").json()
    return dict(account)


def test_disconnecting_revokes_the_sign_in_at_google(gmail: TestClient, google: _Google) -> None:
    account = _signed_in(gmail)

    gmail.delete(f"/v1/mail/accounts/{account['id']}", params={"workspace": "keep"})

    assert google.revoked == [REFRESH]
    assert gmail.get("/v1/mail/accounts").json() == []


def test_a_sign_in_not_started_here_connects_nothing(gmail: TestClient) -> None:
    page = gmail.get("/v1/mail/google/callback", params={"state": "invented", "code": "x"})

    assert "not connected" in page.text and gmail.get("/v1/mail/accounts").json() == []


def test_without_googles_client_the_page_is_told_to_use_imap(
    workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace_demo, "google_sign_in", lambda: None)

    refused = TestClient(create_app(workspace_demo), base_url=BASE).post(
        "/v1/mail/google/connect")

    assert refused.status_code == 404 and "IMAP" in refused.json()["detail"]

"""Outlook through the server: signed in to Microsoft in the browser (personal or work),
counted before anything is read, read into a workspace of its own like any mail account, and
the refresh token Microsoft hands back with each refresh kept as the next one. A fake Microsoft
and Graph; invented mail."""
# ruff: noqa: F811 -- the workspace fixtures are test_workspaces's, used by name
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from graphme.adapters.sources.microsoft_sign_in import TOKEN, MicrosoftSignIn
from graphme.adapters.sources.sign_in import FileVault
from graphme.server import create_app
from graphme.server.mail_accounts import MailAccounts
from graphme.server.state import Demo
from tests.public.server.conftest import read_as, until
from tests.public.server.test_workspaces import BASE, home, workspace_demo  # noqa: F401
from tests.public.test_outlook_mail import _Graph


class _Microsoft(_Graph):
    """Microsoft's token endpoint in front of the fake Graph: each refresh hands back the next
    refresh token."""

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN:
            self.refreshes += 1
            return httpx.Response(200, json={"access_token": "invented-access", "expires_in": 1,
                                             "refresh_token": f"refresh-{self.refreshes}"})
        return super().__call__(request)


@pytest.fixture
def microsoft() -> _Microsoft:
    return _Microsoft()


@pytest.fixture
def outlook(workspace_demo: Demo, microsoft: _Microsoft, home: Path,
            monkeypatch: pytest.MonkeyPatch) -> TestClient:
    http = httpx.Client(transport=httpx.MockTransport(microsoft))
    sign_in = MicrosoftSignIn("invented-client", http)
    workspace_demo._mail = MailAccounts(  # type: ignore[attr-defined]
        home, workspace_demo.workspace_registry(), lambda fallback, account: FileVault(fallback),
        microsoft=sign_in, http=http)
    monkeypatch.setattr(workspace_demo, "microsoft_sign_in", lambda: sign_in)
    monkeypatch.setattr("graphme.processes.open_in_browser", lambda url: None)
    workspace_demo._run = read_as("a", "m.eml")  # type: ignore[method-assign]
    return TestClient(create_app(workspace_demo), base_url=BASE)


def _signed_in(outlook: TestClient) -> dict[str, object]:
    url = outlook.post("/v1/mail/microsoft/connect").json()["url"]
    asked = parse_qs(urlsplit(url).query)
    assert asked["redirect_uri"] == ["http://localhost:8765/v1/mail/microsoft/callback"]
    page = outlook.get("/v1/mail/microsoft/callback",
                       params={"state": asked["state"][0], "code": "invented"})
    assert "kim@invented.test is connected" in page.text
    [account] = outlook.get("/v1/mail/accounts").json()
    return dict(account)


def test_signing_in_makes_a_mail_workspace_and_counts_without_reading(
    outlook: TestClient, microsoft: _Microsoft
) -> None:
    account = _signed_in(outlook)

    assert account["kind"] == "outlook" and account["state"] == "counted"
    assert account["count"] == {"messages": 3, "attachments": 1, "attachment_bytes": None,
                                "oldest": "2026-01-01"}
    assert microsoft.fetched == []


def test_reading_fetches_the_mail_and_keeps_the_latest_refresh_token(
    outlook: TestClient, microsoft: _Microsoft, home: Path
) -> None:
    account = _signed_in(outlook)

    job = outlook.post(f"/v1/mail/accounts/{account['id']}/read", json={"since": None}).json()

    assert until(lambda: outlook.get(f"/v1/jobs/{job['id']}").json()["state"] == "done")
    assert len(microsoft.fetched) == 3
    kept = (home / ".graphme" / "mail" / str(account["id"])).read_text()
    assert kept == f"refresh-{microsoft.refreshes}"  # the newest, not the first one handed back


def test_a_company_that_has_not_approved_the_app_connects_nothing(outlook: TestClient) -> None:
    url = outlook.post("/v1/mail/microsoft/connect").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]

    page = outlook.get("/v1/mail/microsoft/callback", params={
        "state": state, "error": "access_denied", "error_description": "admin approval required"})

    assert "not connected" in page.text and "admin" in page.text
    assert outlook.get("/v1/mail/accounts").json() == []

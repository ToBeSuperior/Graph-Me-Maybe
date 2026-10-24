"""Signing in to Google Drive for the picked files, against a fake Google. Invented values."""
from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from graphme.adapters.sources.google import (
    REVOKE,
    TOKEN,
    Client,
    FileVault,
    GoogleAccount,
    NotConnected,
)

CLIENT = Client(id="invented-client.apps.googleusercontent.com", secret="invented-secret")
LOOPBACK = "http://127.0.0.1:8765/v1/cloud/google/callback"


class _Google:
    """Google's token endpoint, as far as a sign-in uses it."""

    def __init__(self) -> None:
        self.asked: list[dict[str, list[str]]] = []
        self.revoked = False
        self.refresh_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.asked.append(form)
        if str(request.url) == REVOKE:
            self.revoked = True
            return httpx.Response(200)
        assert str(request.url) == TOKEN
        if form["grant_type"] == ["refresh_token"] and not self.refresh_ok:
            return httpx.Response(400, json={"error": "invalid_grant"})
        answer = {"access_token": f"access-{len(self.asked)}", "expires_in": 3600}
        if form["grant_type"] == ["authorization_code"]:
            answer["refresh_token"] = "invented-refresh"
        return httpx.Response(200, json=answer)


def _account(tmp_path: Path, google: _Google, clock: list[float]) -> GoogleAccount:
    return GoogleAccount(CLIENT, httpx.Client(transport=httpx.MockTransport(google)),
                         FileVault(tmp_path / "google.token"), now=lambda: clock[0])


def test_a_sign_in_asks_for_the_picked_files_only_and_proves_it_started_here(
    tmp_path: Path,
) -> None:
    google, clock = _Google(), [1000.0]
    account = _account(tmp_path, google, clock)

    asked = parse_qs(urlsplit(account.start(LOOPBACK)).query)

    assert asked["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert asked["trigger_onepick"] == ["true"] and asked["redirect_uri"] == [LOOPBACK]
    picked = account.finish(state=asked["state"][0], code="invented-code",
                            picked_file_ids="file-a,file-b")

    assert picked.file_ids == ("file-a", "file-b") and account.connected()
    exchange = google.asked[-1]
    verifier = exchange["code_verifier"][0]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert asked["code_challenge"] == [digest.decode()]  # PKCE: the code alone is no use
    # its owner's alone; on Windows the folder's own rights say so, not mode bits
    assert sys.platform == "win32" or (tmp_path / "google.token").stat().st_mode & 0o077 == 0


def test_an_answer_this_app_did_not_ask_for_is_refused(tmp_path: Path) -> None:
    google, clock = _Google(), [1000.0]
    account = _account(tmp_path, google, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError):
        account.finish(state="someone-elses", code="invented-code", picked_file_ids="x")
    clock[0] += 3600  # started an hour ago
    with pytest.raises(ValueError):
        account.finish(state=state, code="invented-code", picked_file_ids="x")
    assert not account.connected()


def test_an_access_token_is_kept_until_it_expires_and_a_revoked_sign_in_is_forgotten(
    tmp_path: Path,
) -> None:
    google, clock = _Google(), [1000.0]
    account = _account(tmp_path, google, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]
    account.finish(state=state, code="invented-code", picked_file_ids="")

    first = account.access_token()
    assert account.access_token() == first  # still valid: not asked again
    clock[0] += 3600
    google.refresh_ok = False  # revoked at Google meanwhile
    with pytest.raises(NotConnected):
        account.access_token()
    assert not account.connected()


def test_disconnecting_revokes_at_google_and_forgets_here(tmp_path: Path) -> None:
    google, clock = _Google(), [1000.0]
    account = _account(tmp_path, google, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]
    account.finish(state=state, code="invented-code", picked_file_ids="")

    account.disconnect()

    assert google.revoked and not account.connected()

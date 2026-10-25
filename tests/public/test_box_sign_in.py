"""Signing in to Box, read-only, against a fake Box. Invented values."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from graphme.adapters.sources.box_account import AUTH, REVOKE, TOKEN, BoxAccount
from graphme.adapters.sources.google import Client, FileVault, NotConnected

CLIENT = Client(id="invented-box-client", secret="invented-box-secret")
LOOPBACK = "http://127.0.0.1:8765/v1/cloud/box/callback"


class _Box:
    """Box's token and revoke endpoints, as far as a sign-in uses them. Every refresh hands
    back a new refresh token: Box's are good for one use."""

    def __init__(self) -> None:
        self.asked: list[dict[str, list[str]]] = []
        self.revoked: list[str] = []
        self.refresh_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.asked.append(form)
        if str(request.url) == REVOKE:
            self.revoked.append(form["token"][0])
            return httpx.Response(200)
        assert str(request.url) == TOKEN
        assert form["client_secret"] == ["invented-box-secret"]
        if form["grant_type"] == ["refresh_token"] and not self.refresh_ok:
            return httpx.Response(400, json={"error": "invalid_grant"})
        n = len(self.asked)
        return httpx.Response(200, json={"access_token": f"access-{n}", "expires_in": 3600,
                                         "refresh_token": f"refresh-{n}"})


def _account(tmp_path: Path, box: _Box, clock: list[float]) -> BoxAccount:
    return BoxAccount(CLIENT, httpx.Client(transport=httpx.MockTransport(box)),
                      FileVault(tmp_path / "box.token"), now=lambda: clock[0])


def test_a_sign_in_asks_to_read_only_and_proves_it_started_here(tmp_path: Path) -> None:
    box, clock = _Box(), [1000.0]
    account = _account(tmp_path, box, clock)

    url = account.start(LOOPBACK)
    asked = parse_qs(urlsplit(url).query)

    assert url.startswith(AUTH)
    assert asked["scope"] == ["root_readonly"] and asked["redirect_uri"] == [LOOPBACK]
    account.finish(state=asked["state"][0], code="invented-code")

    assert account.connected()
    assert box.asked[-1]["grant_type"] == ["authorization_code"]
    assert box.asked[-1]["redirect_uri"] == [LOOPBACK]
    assert sys.platform == "win32" or (tmp_path / "box.token").stat().st_mode & 0o077 == 0


def test_an_answer_this_app_did_not_ask_for_or_too_late_is_refused(tmp_path: Path) -> None:
    box, clock = _Box(), [1000.0]
    account = _account(tmp_path, box, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError, match="not started here"):
        account.finish(state="invented-other", code="invented-code")
    clock[0] += 3600
    with pytest.raises(ValueError, match="not started here"):
        account.finish(state=state, code="invented-code")
    assert not account.connected()


def test_box_refusing_the_sign_in_is_said(tmp_path: Path) -> None:
    # A company Box whose admin has not approved the app answers with an error.
    box, clock = _Box(), [1000.0]
    account = _account(tmp_path, box, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError, match="access_denied"):
        account.finish(state=state, code=None, error="access_denied")


def test_each_refresh_keeps_the_new_refresh_token_box_hands_back(tmp_path: Path) -> None:
    box, clock = _Box(), [1000.0]
    account = _account(tmp_path, box, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]
    account.finish(state=state, code="invented-code")
    kept = (tmp_path / "box.token").read_text()

    clock[0] += 4000  # the access token has run out
    token = account.access_token()

    assert token.startswith("access-")
    assert box.asked[-1]["refresh_token"] == [kept]
    assert (tmp_path / "box.token").read_text() != kept  # the used one is spent
    assert account.access_token() == token  # still good: not refreshed again


def test_a_revoked_sign_in_is_forgotten_and_asks_to_connect_again(tmp_path: Path) -> None:
    box, clock = _Box(), [1000.0]
    account = _account(tmp_path, box, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]
    account.finish(state=state, code="invented-code")
    box.refresh_ok = False
    clock[0] += 4000

    with pytest.raises(NotConnected):
        account.access_token()
    assert not account.connected()


def test_disconnecting_revokes_at_box_and_forgets_here(tmp_path: Path) -> None:
    box, clock = _Box(), [1000.0]
    account = _account(tmp_path, box, clock)
    state = parse_qs(urlsplit(account.start(LOOPBACK)).query)["state"][0]
    account.finish(state=state, code="invented-code")
    kept = (tmp_path / "box.token").read_text()

    account.disconnect()

    assert box.revoked == [kept] and not account.connected()

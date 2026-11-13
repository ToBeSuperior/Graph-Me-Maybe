"""Signing in to Google to read Gmail, read-only, against a fake Google. Invented values."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from graphme.adapters.sources.google_sign_in import AUTH, REVOKE, SCOPE, TOKEN, GoogleSignIn
from graphme.adapters.sources.sign_in import Client, NotConnected

CLIENT = Client(id="invented-google-client", secret="invented-google-secret")
LOOPBACK = "http://127.0.0.1:8765/v1/mail/google/callback"


class _Google:
    """Google's token and revoke endpoints, as far as a sign-in uses them. A refresh token is
    good until revoked: a refresh hands back an access token only."""

    def __init__(self) -> None:
        self.asked: list[dict[str, list[str]]] = []
        self.revoked: list[str] = []
        self.refresh_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.asked.append(form)
        if str(request.url).startswith(REVOKE):
            self.revoked.append(form["token"][0])
            return httpx.Response(200)
        assert str(request.url) == TOKEN
        assert form["client_secret"] == ["invented-google-secret"]
        n = len(self.asked)
        if form["grant_type"] == ["refresh_token"]:
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": f"access-{n}", "expires_in": 3600})
        return httpx.Response(200, json={"access_token": f"access-{n}", "expires_in": 3600,
                                         "refresh_token": "invented-refresh"})


def _sign_in(google: _Google, clock: list[float]) -> GoogleSignIn:
    return GoogleSignIn(CLIENT, httpx.Client(transport=httpx.MockTransport(google)),
                        now=lambda: clock[0])


def test_a_sign_in_asks_to_read_gmail_only_offline_and_proves_it_with_pkce() -> None:
    google, clock = _Google(), [1000.0]
    sign_in = _sign_in(google, clock)

    url = sign_in.start(LOOPBACK)
    asked = parse_qs(urlsplit(url).query)
    refresh = sign_in.finish(state=asked["state"][0], code="invented-code")

    assert url.startswith(AUTH)
    assert asked["scope"] == [SCOPE] and SCOPE.endswith("gmail.readonly")
    assert asked["access_type"] == ["offline"] and asked["redirect_uri"] == [LOOPBACK]
    assert asked["code_challenge_method"] == ["S256"]
    exchanged = google.asked[-1]
    assert exchanged["grant_type"] == ["authorization_code"]
    verifier = exchanged["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert asked["code_challenge"] == [challenge.rstrip(b"=").decode()]
    assert refresh == "invented-refresh"


def test_an_answer_this_app_did_not_ask_for_or_too_late_is_refused() -> None:
    google, clock = _Google(), [1000.0]
    sign_in = _sign_in(google, clock)
    state = parse_qs(urlsplit(sign_in.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError, match="not started here"):
        sign_in.finish(state="invented-other", code="invented-code")
    clock[0] += 3600
    with pytest.raises(ValueError, match="not started here"):
        sign_in.finish(state=state, code="invented-code")
    assert google.asked == []


def test_google_refusing_the_sign_in_is_said() -> None:
    google, clock = _Google(), [1000.0]
    sign_in = _sign_in(google, clock)
    state = parse_qs(urlsplit(sign_in.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError, match="access_denied"):
        sign_in.finish(state=state, code=None, error="access_denied")


def test_an_access_token_is_kept_until_it_runs_out() -> None:
    google, clock = _Google(), [1000.0]
    sign_in = _sign_in(google, clock)

    first = sign_in.access_token("invented-refresh")
    again = sign_in.access_token("invented-refresh")
    clock[0] += 4000
    later = sign_in.access_token("invented-refresh")

    assert first == again != later
    assert [a["grant_type"] for a in google.asked] == [["refresh_token"], ["refresh_token"]]


def test_a_revoked_sign_in_asks_to_connect_again() -> None:
    google, clock = _Google(), [1000.0]
    google.refresh_ok = False

    with pytest.raises(NotConnected, match="connect"):
        _sign_in(google, clock).access_token("invented-refresh")


def test_revoking_tells_google() -> None:
    google, clock = _Google(), [1000.0]

    _sign_in(google, clock).revoke("invented-refresh")

    assert google.revoked == ["invented-refresh"]

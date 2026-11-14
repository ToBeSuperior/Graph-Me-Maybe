"""Signing in to Microsoft to read Outlook mail, personal or work, read-only, against a fake
Microsoft. Invented values."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from graphme.adapters.sources.microsoft_sign_in import AUTH, SCOPES, TOKEN, MicrosoftSignIn
from graphme.adapters.sources.sign_in import NotConnected

LOOPBACK = "http://localhost:8765/v1/mail/microsoft/callback"


class _Microsoft:
    """Microsoft's token endpoint as a sign-in uses it. A public client sends no secret, and
    every refresh hands back the next refresh token."""

    def __init__(self) -> None:
        self.asked: list[dict[str, list[str]]] = []
        self.refresh_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TOKEN
        form = parse_qs(request.content.decode())
        self.asked.append(form)
        assert "client_secret" not in form
        n = len(self.asked)
        if form["grant_type"] == ["refresh_token"] and not self.refresh_ok:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": f"access-{n}", "expires_in": 3600,
                                         "refresh_token": f"refresh-{n}"})


def _sign_in(microsoft: _Microsoft, clock: list[float]) -> MicrosoftSignIn:
    http = httpx.Client(transport=httpx.MockTransport(microsoft))
    return MicrosoftSignIn("invented-client", http, now=lambda: clock[0])


def test_a_sign_in_takes_personal_and_work_accounts_asks_to_read_mail_with_pkce() -> None:
    microsoft, clock = _Microsoft(), [1000.0]
    sign_in = _sign_in(microsoft, clock)

    url = sign_in.start(LOOPBACK)
    asked = parse_qs(urlsplit(url).query)
    refresh = sign_in.finish(state=asked["state"][0], code="invented-code")

    assert url.startswith(AUTH) and "/common/" in AUTH  # personal and work accounts both
    assert asked["scope"] == [SCOPES] and "Mail.Read" in SCOPES and "offline_access" in SCOPES
    assert asked["redirect_uri"] == [LOOPBACK] and asked["code_challenge_method"] == ["S256"]
    exchanged = microsoft.asked[-1]
    verifier = exchanged["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert asked["code_challenge"] == [challenge.rstrip(b"=").decode()]
    assert exchanged["grant_type"] == ["authorization_code"] and refresh == "refresh-1"


def test_an_answer_this_app_did_not_ask_for_or_too_late_is_refused() -> None:
    microsoft, clock = _Microsoft(), [1000.0]
    sign_in = _sign_in(microsoft, clock)
    state = parse_qs(urlsplit(sign_in.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError, match="not started here"):
        sign_in.finish(state="invented-other", code="invented-code")
    clock[0] += 3600
    with pytest.raises(ValueError, match="not started here"):
        sign_in.finish(state=state, code="invented-code")


def test_a_company_that_has_not_approved_the_app_is_said_so() -> None:
    microsoft, clock = _Microsoft(), [1000.0]
    sign_in = _sign_in(microsoft, clock)
    state = parse_qs(urlsplit(sign_in.start(LOOPBACK)).query)["state"][0]

    with pytest.raises(ValueError, match="admin"):
        sign_in.finish(state=state, code=None, error="access_denied",
                       description="AADSTS90094: admin approval required")


def test_each_refresh_keeps_the_refresh_token_microsoft_hands_back() -> None:
    microsoft, clock = _Microsoft(), [1000.0]
    sign_in = _sign_in(microsoft, clock)
    kept: list[str] = []

    first = sign_in.access_token("refresh-0", keep=kept.append)
    again = sign_in.access_token("refresh-0", keep=kept.append)
    clock[0] += 4000
    later = sign_in.access_token("refresh-0", keep=kept.append)

    assert first == again != later and kept == ["refresh-1", "refresh-2"]


def test_a_revoked_sign_in_asks_to_connect_again() -> None:
    microsoft, clock = _Microsoft(), [1000.0]
    microsoft.refresh_ok = False

    with pytest.raises(NotConnected, match="connect"):
        _sign_in(microsoft, clock).access_token("refresh-0", keep=lambda token: None)

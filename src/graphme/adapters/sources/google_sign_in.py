"""Signing in to Google to read Gmail, and nothing else.

The project's own Google OAuth client (the Desktop app type) asks for `gmail.readonly`: the app
reads, never sends, moves or deletes. Google sends the browser back to this machine's loopback
address with a code, exchanged here with PKCE's verifier (and the client's secret, which Google
asks of a Desktop client though it proves nothing there). `access_type=offline` hands back a
refresh token, good until revoked; the caller keeps it in the credential store
(`sign_in.vault`) -- never in `.env`, never sent to a page.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from graphme.adapters.sources.sign_in import PENDING_SECONDS, Client, NotConnected

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GoogleSignIn:
    """A sign-in started in the browser, finished when Google sends the browser back, and an
    access token for each refresh token on demand."""

    def __init__(self, client: Client, http: httpx.Client, *,
                 now: Callable[[], float] = time.time) -> None:
        self._client = client
        self._http = http
        self._now = now
        self._pending: dict[str, tuple[str, str, float]] = {}
        """state -> (redirect_uri, PKCE verifier, when started)."""
        self._access: dict[str, tuple[str, float]] = {}
        """refresh token -> (access token, when it runs out)."""
        self._lock = threading.Lock()

    def start(self, redirect_uri: str) -> str:
        """The address to open in the browser to sign in."""
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        with self._lock:
            self._pending = {s: p for s, p in self._pending.items()
                             if self._now() - p[2] < PENDING_SECONDS}
            self._pending[state] = (redirect_uri, verifier, self._now())
        return AUTH + "?" + urlencode({
            "client_id": self._client.id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPE, "state": state, "access_type": "offline", "prompt": "consent",
            "code_challenge": challenge.rstrip(b"=").decode(), "code_challenge_method": "S256"})

    def finish(self, *, state: str, code: str | None, error: str | None = None) -> str:
        """Google's answer: the code exchanged, and the refresh token handed back. A state this
        process did not start, or one started too long ago, is refused; so is Google's own
        refusal, as it said it."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self._now() - pending[2] >= PENDING_SECONDS:
            raise ValueError("this sign-in was not started here, or took too long -- start again")
        if error or not code:
            raise ValueError(f"Google did not sign in: {error or 'no code'}")
        answer = self._token({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": pending[0], "code_verifier": pending[1]})
        refresh = answer.get("refresh_token")
        if not refresh:
            raise ValueError("Google handed back no refresh token -- sign in again")
        self._keep(str(refresh), answer)
        return str(refresh)

    def access_token(self, refresh: str) -> str:
        """A token for the Gmail API, refreshed when it is about to run out."""
        with self._lock:
            kept = self._access.get(refresh)
        if kept is not None and self._now() < kept[1] - 60:
            return kept[0]
        try:
            answer = self._token({"grant_type": "refresh_token", "refresh_token": refresh})
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 401):  # revoked, or expired
                raise NotConnected("the Google sign-in expired -- connect the account "
                                   "again") from error
            raise
        return self._keep(refresh, answer)

    def revoke(self, refresh: str) -> None:
        """The refresh token revoked at Google; forgotten here whatever Google says."""
        with contextlib.suppress(httpx.HTTPError):
            self._http.post(REVOKE, data={"token": refresh}, timeout=30)
        with self._lock:
            self._access.pop(refresh, None)

    def _keep(self, refresh: str, answer: dict[str, object]) -> str:
        token = str(answer["access_token"])
        with self._lock:
            self._access[refresh] = (token, self._now() + float(str(answer.get("expires_in",
                                                                                3600))))
        return token

    def _token(self, fields: dict[str, str]) -> dict[str, object]:
        form = {"client_id": self._client.id, "client_secret": self._client.secret or "",
                **fields}
        response = self._http.post(TOKEN, data=form, timeout=30)
        response.raise_for_status()
        answer: dict[str, object] = json.loads(response.text)
        return answer

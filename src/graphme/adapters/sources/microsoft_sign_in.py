"""Signing in to Microsoft to read Outlook mail -- a personal account (Outlook.com, Hotmail) or a
work or school one (Microsoft 365) -- and nothing else.

The project's own app registration is a public client: no secret, PKCE, and the browser sent
back to this machine's loopback address (`http://localhost`, any port, as Microsoft allows a
desktop app). It asks for `Mail.Read` and `offline_access`: the app reads, never sends, moves or
deletes. A company may need its admin's approval the first time; that is said as Microsoft said
it. Microsoft hands back a new refresh token with every refresh; the caller keeps each one in the
credential store (`sign_in.vault`) -- never in `.env`, never sent to a page.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from graphme.adapters.sources.sign_in import PENDING_SECONDS, NotConnected

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
"""`common`: personal accounts and every company's accounts both."""
AUTH = f"{AUTHORITY}/authorize"
TOKEN = f"{AUTHORITY}/token"
SCOPES = ("offline_access https://graph.microsoft.com/Mail.Read "
          "https://graph.microsoft.com/User.Read")


class MicrosoftSignIn:
    """A sign-in started in the browser, finished when Microsoft sends the browser back, and an
    access token for each refresh token on demand."""

    def __init__(self, client_id: str, http: httpx.Client, *,
                 now: Callable[[], float] = time.time) -> None:
        self._client_id = client_id
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
            "client_id": self._client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "response_mode": "query", "scope": SCOPES, "state": state, "prompt": "select_account",
            "code_challenge": challenge.rstrip(b"=").decode(), "code_challenge_method": "S256"})

    def finish(self, *, state: str, code: str | None, error: str | None = None,
               description: str | None = None) -> str:
        """Microsoft's answer: the code exchanged, and the refresh token handed back. A state
        this process did not start, or one started too long ago, is refused; so is Microsoft's
        own refusal, as it said it -- a company that has not approved the app among them."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self._now() - pending[2] >= PENDING_SECONDS:
            raise ValueError("this sign-in was not started here, or took too long -- start again")
        if error or not code:
            said = f"{error or 'no code'}{f': {description}' if description else ''}"
            raise ValueError(f"Microsoft did not sign in: {said} -- a work account may need its "
                             "admin to approve Graph-Me-Maybe once")
        answer = self._token({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": pending[0], "code_verifier": pending[1]})
        refresh = answer.get("refresh_token")
        if not refresh:
            raise ValueError("Microsoft handed back no refresh token -- sign in again")
        self._keep(str(refresh), answer)
        return str(refresh)

    def access_token(self, refresh: str, *, keep: Callable[[str], None]) -> str:
        """A token for Microsoft Graph, refreshed when it is about to run out; the refresh token
        Microsoft hands back with it goes to `keep`, as the one to use next."""
        with self._lock:
            kept = self._access.get(refresh)
        if kept is not None and self._now() < kept[1] - 60:
            return kept[0]
        try:
            answer = self._token({"grant_type": "refresh_token", "refresh_token": refresh,
                                  "scope": SCOPES})
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 401):  # revoked, or expired
                raise NotConnected("the Microsoft sign-in expired -- connect the account "
                                   "again") from error
            raise
        token = self._keep(refresh, answer)
        if (fresh := answer.get("refresh_token")) and fresh != refresh:
            keep(str(fresh))
        return token

    def _keep(self, refresh: str, answer: dict[str, object]) -> str:
        token = str(answer["access_token"])
        expires = float(str(answer.get("expires_in", 3600)))
        with self._lock:
            self._access[refresh] = (token, self._now() + expires)
        return token

    def _token(self, fields: dict[str, str]) -> dict[str, object]:
        response = self._http.post(TOKEN, data={"client_id": self._client_id, **fields},
                                   timeout=30)
        response.raise_for_status()
        answer: dict[str, object] = json.loads(response.text)
        return answer

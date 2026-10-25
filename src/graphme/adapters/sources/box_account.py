"""Signing in to Box, to read what the user picks there and nothing else.

The user's own Box app (OAuth 2.0, user authentication) asks for `root_readonly`: the app reads,
never writes. Box sends the browser back to this machine's loopback address -- Box takes any
port there -- with a code, exchanged here with the app's client secret, which Box requires.
A company Box may answer that its admin has not approved the app; that is said as Box said it.

Box's refresh tokens are good for one use: each refresh hands back the next one, kept at once.
It is kept as Google Drive's is (`google.vault`): the OS credential store, else a file only its
owner can read -- never in `.env`, never sent to a page.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from graphme.adapters.sources.google import PENDING_SECONDS, Client, NotConnected, Vault

AUTH = "https://account.box.com/api/oauth2/authorize"
TOKEN = "https://api.box.com/oauth2/token"
REVOKE = "https://api.box.com/oauth2/revoke"
SCOPE = "root_readonly"
ACCOUNT = "box"
"""Where the refresh token is kept in the credential store, beside Google Drive's."""


class BoxAccount:
    """The signed-in Box account: started in the browser, finished when Box sends the browser
    back, an access token on demand, and a way out."""

    def __init__(self, client: Client, http: httpx.Client, keep: Vault, *,
                 now: Callable[[], float] = time.time) -> None:
        self._client = client
        self._http = http
        self._vault = keep
        self._now = now
        self._pending: dict[str, tuple[str, float]] = {}
        """state -> (redirect_uri, when started)."""
        self._access: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def start(self, redirect_uri: str) -> str:
        """The address to open in the browser to sign in."""
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._pending = {s: p for s, p in self._pending.items()
                             if self._now() - p[1] < PENDING_SECONDS}
            self._pending[state] = (redirect_uri, self._now())
        return AUTH + "?" + urlencode({
            "client_id": self._client.id, "redirect_uri": redirect_uri,
            "response_type": "code", "scope": SCOPE, "state": state})

    def finish(self, *, state: str, code: str | None, error: str | None = None) -> None:
        """Box's answer: the code exchanged for tokens. A state this process did not start, or
        one started too long ago, is refused; so is Box's own refusal, as it said it."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self._now() - pending[1] >= PENDING_SECONDS:
            raise ValueError("this sign-in was not started here, or took too long -- start again")
        if error or not code:
            raise ValueError(f"Box did not sign in: {error or 'no code'} -- a company Box may "
                             "need its admin to approve the app")
        self._token({"grant_type": "authorization_code", "code": code,
                     "redirect_uri": pending[0]})

    def connected(self) -> bool:
        return self._vault.get() is not None

    def access_token(self) -> str:
        """A token for the Box API, refreshed when it is about to expire."""
        with self._lock:
            if self._access is not None and self._now() < self._access[1] - 60:
                return self._access[0]
            refresh = self._vault.get()
            if refresh is None:
                raise NotConnected("Box is not connected")
            try:  # under the lock: a refresh token spent twice is refused the second time
                answer = self._token({"grant_type": "refresh_token", "refresh_token": refresh},
                                     locked=True)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in (400, 401):  # revoked, or expired
                    self._vault.clear()
                    raise NotConnected("the Box sign-in expired -- connect again") from error
                raise
        return str(answer["access_token"])

    def disconnect(self) -> None:
        """The token revoked at Box and forgotten here."""
        refresh = self._vault.get()
        if refresh is not None:
            with contextlib.suppress(httpx.HTTPError):  # forgotten here whatever Box says
                self._http.post(REVOKE, data={"client_id": self._client.id,
                                              "client_secret": self._client.secret or "",
                                              "token": refresh}, timeout=30)
        self._vault.clear()
        with self._lock:
            self._access = None

    def _token(self, fields: dict[str, str], *, locked: bool = False) -> dict[str, object]:
        form = {"client_id": self._client.id, "client_secret": self._client.secret or "",
                **fields}
        response = self._http.post(TOKEN, data=form, timeout=30)
        response.raise_for_status()
        answer: dict[str, object] = json.loads(response.text)
        if refresh := answer.get("refresh_token"):
            self._vault.put(str(refresh))  # the one just used is spent
        if token := answer.get("access_token"):
            expires = float(str(answer.get("expires_in", 3600)))
            if locked:
                self._access = (str(token), self._now() + expires)
            else:
                with self._lock:
                    self._access = (str(token), self._now() + expires)
        return answer

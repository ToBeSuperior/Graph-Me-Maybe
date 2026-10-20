"""Signing in to Google Drive for the files the user picks, and nothing else.

The app asks for `drive.file` only: it reads what the user chose in Google's own picker,
opened in the browser with the sign-in (`trigger_onepick`), and no Google security assessment
is needed for it. Google sends the browser back to this machine's loopback address with the
picked ids and a code, which is exchanged for tokens here (PKCE: the code is no use without
the verifier this process kept).

The refresh token is kept in the OS credential store (Windows Credential Manager, macOS
Keychain) -- never in `.env`, never sent to a page. Where there is no credential store, a file
only its owner can read under the app's folder holds it.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
SCOPE = "https://www.googleapis.com/auth/drive.file"
SERVICE = "graph-me-maybe"
ACCOUNT = "google-drive"
PENDING_SECONDS = 600
"""How long a sign-in started in the browser may take before its answer is refused."""


class NotConnected(LookupError):
    """No Google account is signed in, or its sign-in was revoked."""


class Vault(Protocol):
    """Where the refresh token is kept."""

    def get(self) -> str | None: ...

    def put(self, token: str) -> None: ...

    def clear(self) -> None: ...


class KeyringVault:
    """The OS credential store."""

    def get(self) -> str | None:
        import keyring

        token: str | None = keyring.get_password(SERVICE, ACCOUNT)
        return token

    def put(self, token: str) -> None:
        import keyring

        keyring.set_password(SERVICE, ACCOUNT, token)

    def clear(self) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(SERVICE, ACCOUNT)


class FileVault:
    """A file only its owner can read, where no credential store is."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self) -> str | None:
        try:
            return self._path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def put(self, token: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as kept:
            kept.write(token)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


def vault(fallback: Path) -> Vault:
    """The OS credential store when it works here, else the owner-only file."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as NoKeyring

        if not isinstance(keyring.get_keyring(), NoKeyring):
            return KeyringVault()
    except Exception:  # noqa: BLE001 -- no usable store is the fallback's case
        pass
    return FileVault(fallback)


@dataclass(frozen=True)
class Client:
    """The user's own OAuth client, of the Desktop app type."""

    id: str
    secret: str | None


@dataclass(frozen=True)
class Picked:
    """What a sign-in brought back: the files the user chose."""

    file_ids: tuple[str, ...]


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class GoogleAccount:
    """The signed-in Google account, as this app holds it: started in the browser, finished
    when Google sends the browser back, an access token on demand, and a way out."""

    def __init__(self, client: Client, http: httpx.Client, keep: Vault, *,
                 now: Callable[[], float] = time.time) -> None:
        self._client = client
        self._http = http
        self._vault = keep
        self._now = now
        self._pending: dict[str, tuple[str, str, float]] = {}
        """state -> (verifier, redirect_uri, when started)."""
        self._access: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def start(self, redirect_uri: str) -> str:
        """The address to open in the browser: sign in and pick files in one go."""
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        with self._lock:
            self._pending = {s: p for s, p in self._pending.items()
                             if self._now() - p[2] < PENDING_SECONDS}
            self._pending[state] = (verifier, redirect_uri, self._now())
        return AUTH + "?" + urlencode({
            "client_id": self._client.id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": _challenge(verifier), "code_challenge_method": "S256",
            "trigger_onepick": "true", "allow_multiple": "true",
            "allow_folder_selection": "true",
        })

    def finish(self, *, state: str, code: str | None, picked_file_ids: str | None,
               error: str | None = None) -> Picked:
        """Google's answer: the code exchanged for tokens, and the files picked. A state this
        process did not start, or one started too long ago, is refused."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self._now() - pending[2] >= PENDING_SECONDS:
            raise ValueError("this sign-in was not started here, or took too long -- start again")
        if error or not code:
            raise ValueError(f"Google did not sign in: {error or 'no code'}")
        verifier, redirect_uri, _ = pending
        answer = self._token({"grant_type": "authorization_code", "code": code,
                              "redirect_uri": redirect_uri, "code_verifier": verifier})
        if refresh := answer.get("refresh_token"):
            self._vault.put(str(refresh))
        ids = tuple(i for i in (picked_file_ids or "").split(",") if i.strip())
        return Picked(file_ids=ids)

    def connected(self) -> bool:
        return self._vault.get() is not None

    def access_token(self) -> str:
        """A token for the Drive API, refreshed when it is about to expire."""
        with self._lock:
            if self._access is not None and self._now() < self._access[1] - 60:
                return self._access[0]
        refresh = self._vault.get()
        if refresh is None:
            raise NotConnected("Google Drive is not connected")
        try:
            answer = self._token({"grant_type": "refresh_token", "refresh_token": refresh})
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 401):  # revoked, or expired
                self._vault.clear()
                raise NotConnected("the Google sign-in expired -- connect again") from error
            raise
        return str(answer["access_token"])

    def disconnect(self) -> None:
        """The token revoked at Google and forgotten here."""
        refresh = self._vault.get()
        if refresh is not None:
            with contextlib.suppress(httpx.HTTPError):  # forgotten here whatever Google says
                self._http.post(REVOKE, data={"token": refresh}, timeout=30)
        self._vault.clear()
        with self._lock:
            self._access = None

    def _token(self, fields: dict[str, str]) -> dict[str, object]:
        form = {"client_id": self._client.id, **fields}
        if self._client.secret:
            form["client_secret"] = self._client.secret
        response = self._http.post(TOKEN, data=form, timeout=30)
        response.raise_for_status()
        answer: dict[str, object] = json.loads(response.text)
        if token := answer.get("access_token"):
            expires = float(str(answer.get("expires_in", 3600)))
            with self._lock:
                self._access = (str(token), self._now() + expires)
        return answer

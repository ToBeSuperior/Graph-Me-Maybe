"""What a sign-in to a cloud drive keeps, and where: the user's own OAuth client, and the
refresh token in the OS credential store (Windows Credential Manager, macOS Keychain) -- never in
`.env`, never sent to a page. Where there is no credential store, a file only its owner can read
under the app's folder holds it.
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SERVICE = "graph-me-maybe"
PENDING_SECONDS = 600
"""How long a sign-in started in the browser may take before its answer is refused."""


class NotConnected(LookupError):
    """No account is signed in, or its sign-in was revoked."""


class Vault(Protocol):
    """Where the refresh token is kept."""

    def get(self) -> str | None: ...

    def put(self, token: str) -> None: ...

    def clear(self) -> None: ...


class KeyringVault:
    """The OS credential store, under `account` (one per service signed in to)."""

    def __init__(self, account: str) -> None:
        self._account = account

    def get(self) -> str | None:
        import keyring

        token: str | None = keyring.get_password(SERVICE, self._account)
        return token

    def put(self, token: str) -> None:
        import keyring

        keyring.set_password(SERVICE, self._account, token)

    def clear(self) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(SERVICE, self._account)


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


def vault(fallback: Path, account: str) -> Vault:
    """The OS credential store when it works here, else the owner-only file."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as NoKeyring

        if not isinstance(keyring.get_keyring(), NoKeyring):
            return KeyringVault(account)
    except Exception:  # noqa: BLE001 -- no usable store is the fallback's case
        pass
    return FileVault(fallback)


@dataclass(frozen=True)
class Client:
    """The user's own OAuth client, of the Desktop app type."""

    id: str
    secret: str | None

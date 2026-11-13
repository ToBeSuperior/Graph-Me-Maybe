"""The mail accounts connected, each read into a workspace of its own.

`<home>/mail_accounts.json` keeps what is not secret (server, address, workspace, how far the
account has been read); the password goes to the credential store only (`sign_in.vault`), and
nothing here answers or logs it. Each account's messages are kept under its workspace's graph
folder (`MailStore`), so deleting the workspace deletes them.
"""
from __future__ import annotations

import imaplib
import json
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from graphme.adapters.sources.gmail_mail import GmailMailbox
from graphme.adapters.sources.google_sign_in import GoogleSignIn
from graphme.adapters.sources.imap_mail import ImapMailbox, MailCount, MailRefused, SyncResult
from graphme.adapters.sources.mail_store import MailStore
from graphme.adapters.sources.sign_in import Vault, vault
from graphme.server.workspaces import Workspaces


class MailAccounts:
    def __init__(
        self, home: Path, workspaces: Workspaces,
        vault_for: Callable[[Path, str], Vault] = vault, *,
        connect: Callable[..., imaplib.IMAP4] = imaplib.IMAP4_SSL,
        google: GoogleSignIn | None = None, http: httpx.Client | None = None,
    ) -> None:
        self._home = home
        self._file = home / "mail_accounts.json"
        self._workspaces = workspaces
        self._vault_for = vault_for
        self._connect = connect
        self._google = google
        self._http = http or httpx.Client()
        self._lock = threading.Lock()

    def listed(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read()

    def get(self, account: str) -> dict[str, Any]:
        found = next((a for a in self.listed() if a["id"] == account), None)
        if found is None:
            raise LookupError(f"no such mail account: {account}")
        return found

    def add(self, host: str, port: int, address: str, password: str) -> dict[str, Any]:
        """Log in, count what the mailbox holds (nothing is read), and keep the account in a
        workspace of its own. ValueError: the server's words when it refuses, or a workspace
        is called `address` already. Nothing is kept then."""
        host, address = host.strip(), address.strip()
        if not (host and address and password):
            raise ValueError("a mail account needs a server, an address and a password")
        try:
            counted = ImapMailbox(host, port, address, password, connect=self._connect).count()
        except MailRefused as error:
            raise ValueError(str(error)) from None
        return self._keep({"kind": "imap", "host": host, "port": port, "address": address},
                          password, counted)

    def add_gmail(self, refresh: str) -> dict[str, Any]:
        """A Gmail account signed in to (its refresh token), counted -- nothing read -- and kept
        in a workspace of its own, as `add` keeps an IMAP one."""
        mailbox = self._gmail(refresh)
        try:
            address = mailbox.address()
            counted = mailbox.count()
        except MailRefused as error:
            raise ValueError(str(error)) from None
        return self._keep({"kind": "gmail", "host": "gmail.googleapis.com", "port": 443,
                           "address": address}, refresh, counted)

    def _keep(self, where: dict[str, Any], secret: str, counted: MailCount) -> dict[str, Any]:
        account = uuid.uuid4().hex[:12]
        workspace = self._workspaces.create(where["address"], kind="mail",
                                            account=account)["id"]
        self._vault(account).put(secret)
        item = {"id": account, **where, "workspace": workspace, "state": "counted",
                "count": _counted(counted), "read_since": None, "last_checked": _now(),
                "error": None}
        with self._lock:
            self._write([*self._read(), item])
        return item

    def _gmail(self, refresh: str) -> GmailMailbox:
        if self._google is None:
            raise LookupError("Gmail sign-in is not set up in this build")
        google = self._google
        return GmailMailbox(self._http, lambda: google.access_token(refresh))

    def _mailbox(self, item: dict[str, Any], secret: str) -> ImapMailbox | GmailMailbox:
        if item.get("kind") == "gmail":
            return self._gmail(secret)
        return ImapMailbox(item["host"], item["port"], item["address"], secret,
                           connect=self._connect)

    def start_reading(self, account: str, since: date | None) -> None:
        """Mail older than `since` is left out (None: none is); `sync` reads by this."""
        self._change(account, state="reading", read_since=since.isoformat() if since else None,
                     error=None)

    def sync(self, account: str, stop: threading.Event | None = None) -> SyncResult:
        """The account's store brought level with its mailbox, newest first, as far back as
        `read_since`. The one way mail is fetched: the first reading and the agent's checks
        both come here. A failure is kept in the account's `error` and raised again."""
        item = self.get(account)
        password = self._vault(account).get()
        try:
            if password is None:
                raise MailRefused("the password is no longer kept -- connect the account again")
            since = date.fromisoformat(item["read_since"]) if item["read_since"] else None
            result = self._mailbox(item, password).sync(self.store(account), since=since,
                                                         stop=stop)
        except Exception as error:  # noqa: BLE001 -- any failure is kept, or "reading" stays
            self._change(account, state="error", error=str(error), last_checked=_now())
            raise
        # A stopped sync is not "current": the rest is read when asked again.
        stopped = stop is not None and stop.is_set()
        self._change(account, state="counted" if stopped else "current", error=None,
                     last_checked=_now())
        return result

    def hold(self, account: str, pending: list[str], gone: list[str]) -> None:
        """Files read in and files removed that the graph has not taken yet (its workspace is
        closed, or a build was running): taken when the workspace is open and idle."""
        self._change(account, pending=pending, gone=gone)

    def set_state(self, account: str, state: str) -> None:
        self._change(account, state=state)

    def fail(self, account: str, error: str) -> None:
        self._change(account, state="error", error=error)

    def store(self, account: str) -> MailStore:
        return MailStore(self.messages(account).parent)

    def messages(self, account: str) -> Path:
        """Where the account's messages are kept, as `.eml` files."""
        graph = self._workspaces.graph(self.get(account)["workspace"])
        assert graph is not None  # a mail workspace is never the home's own
        return graph / "mail" / "messages"

    def forget(self, account: str) -> None:
        """The password and the messages kept are gone; the workspace is the caller's to keep
        or delete."""
        item = self.get(account)
        secret = self._vault(account).get()
        if item.get("kind") == "gmail" and self._google is not None and secret:
            self._google.revoke(secret)  # the sign-in ends at Google too
        self._vault(account).clear()
        shutil.rmtree(self.messages(account).parent, ignore_errors=True)
        with self._lock:
            self._write([a for a in self._read() if a["id"] != item["id"]])

    def _vault(self, account: str) -> Vault:
        return self._vault_for(self._home / ".graphme" / "mail" / account, f"mail-{account}")

    def _change(self, account: str, **fields: Any) -> None:
        with self._lock:
            items = self._read()
            for item in items:
                if item["id"] == account:
                    item.update(fields)
            self._write(items)

    def _read(self) -> list[dict[str, Any]]:
        try:
            said = json.loads(self._file.read_text(encoding="utf-8"))
            return [dict(a) for a in said["accounts"]]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self._home.mkdir(parents=True, exist_ok=True)
        kept = self._file.with_suffix(".tmp")
        kept.write_text(json.dumps({"accounts": items}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        kept.replace(self._file)


def _counted(count: MailCount) -> dict[str, Any]:
    return {**asdict(count), "oldest": count.oldest.isoformat() if count.oldest else None}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

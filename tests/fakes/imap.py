"""A fake `imaplib.IMAP4` holding folders of invented messages.

It implements only what `ImapMailbox` calls (login, logout, list, select, response, uid SEARCH
and FETCH) and records every call, so a test can assert a folder was opened read-only and
`BODY.PEEK[]` was never asked for while counting. The password is never recorded.
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email import message_from_bytes
from email.message import Message
from typing import Any


@dataclass
class FakeFolder:
    flags: str = ""
    uidvalidity: int = 1
    messages: dict[int, tuple[bytes, date]] = field(default_factory=dict)
    next_uid: int = 1
    drafts: set[int] = field(default_factory=set)  # flagged \Draft


def _structure(part: Message) -> str:
    if part.is_multipart():
        return "(" + "".join(_structure(each) for each in part.get_payload()) + \
            f' "{part.get_content_subtype().upper()}")'
    kind, subtype = part.get_content_maintype().upper(), part.get_content_subtype().upper()
    name = part.get_filename()
    params = f'("NAME" "{name}")' if name else "NIL"
    disposition = part.get_content_disposition()
    dispo = f'("{disposition.upper()}" NIL)' if disposition else "NIL"
    size = len(part.get_payload(decode=True) or b"")
    lines = " 1" if kind == "TEXT" else ""
    return f'("{kind}" "{subtype}" {params} NIL NIL "7BIT" {size}{lines} NIL {dispo})'


class FakeImap:
    """Callable like `imaplib.IMAP4_SSL`: `ImapMailbox(..., connect=fake)` gets the fake back."""

    def __init__(self, folders: dict[str, FakeFolder] | None = None) -> None:
        self.folders = folders or {}
        self.calls: list[tuple[Any, ...]] = []
        self.login_error: str | None = None
        self.fail_fetch_after: int | None = None  # OSError on the fetch after this many
        self.fail_fetch_error: Exception | None = None  # raised on the first fetch
        self.fetched = 0
        self._selected: str | None = None

    def __call__(self, host: str, port: int, timeout: int = 30) -> FakeImap:
        self.calls.append(("connect", host, port))
        return self

    def add(self, folder: str, raw: bytes, *, day: date = date(2026, 1, 1),
            draft: bool = False) -> int:
        each = self.folders[folder]
        uid, each.next_uid = each.next_uid, each.next_uid + 1
        each.messages[uid] = (raw, day)
        if draft:
            each.drafts.add(uid)
        return uid

    def renumber(self, folder: str) -> None:
        """What a server does when it rebuilds a folder: new UIDVALIDITY, new UIDs."""
        each = self.folders[folder]
        each.uidvalidity += 1
        old, each.messages = each.messages, {}
        each.next_uid += 100
        for raw, day in old.values():
            self.add(folder, raw, day=day)

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", user))
        if self.login_error:
            raise imaplib.IMAP4.error(self.login_error)
        return "OK", [b"logged in"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout",))
        return "BYE", [b""]

    def list(self) -> tuple[str, list[bytes]]:
        self.calls.append(("list",))
        return "OK", [f'({each.flags}) "/" "{name}"'.encode() for name, each in
                      self.folders.items()]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        name = re.sub(r"\\(.)", r"\1", mailbox[1:-1])
        if name not in self.folders:
            return "NO", [b"no such folder"]
        self._selected = name
        return "OK", [str(len(self.folders[name].messages)).encode()]

    def response(self, code: str) -> tuple[str, list[bytes | None]]:
        assert self._selected is not None and code == "UIDVALIDITY"
        return code, [str(self.folders[self._selected].uidvalidity).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.calls.append(("uid", command, *args))
        assert self._selected is not None
        messages = self.folders[self._selected].messages
        if command == "SEARCH":
            criteria = args[1:]
            floor = date.min
            if criteria[0] == "SINCE":
                floor = datetime.strptime(criteria[1], "%d-%b-%Y").date()
            drafts = self.folders[self._selected].drafts if "DRAFT" in criteria else set()
            found = [str(uid) for uid, (_, day) in messages.items()
                     if day >= floor and uid not in drafts]
            return "OK", [" ".join(found).encode()]
        assert command == "FETCH"
        wanted = [int(each) for each in args[0].split(",")]
        if "BODY.PEEK[]" in args[1]:
            if self.fail_fetch_error is not None:
                raise self.fail_fetch_error
            if self.fail_fetch_after is not None and self.fetched >= self.fail_fetch_after:
                raise OSError("connection dropped")
            self.fetched += 1
            return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(messages[uid][0])}}}".encode(),
                           messages[uid][0]) for uid in wanted if uid in messages][0:1] + [b")"]
        rows = []
        for uid in wanted:
            raw, day = messages[uid]
            stamp = day.strftime("%d-%b-%Y") + " 00:00:00 +0000"
            structure = _structure(message_from_bytes(raw))
            rows.append(f'{uid} (UID {uid} INTERNALDATE "{stamp}" '
                        f'BODYSTRUCTURE {structure})'.encode())
        return "OK", rows

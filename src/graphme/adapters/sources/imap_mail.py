"""An IMAP mailbox read into a `MailStore`: read-only, newest first, resumable.

Folders are opened with `select(readonly=True)` and messages fetched with `BODY.PEEK[]`, so
nothing is marked read. Spam, trash and drafts are left out. A server listing a folder flagged
`\\All` (Gmail's All Mail) is read from that folder alone, its drafts left out: it holds every
label, the archived mail and the sent once each, and no spam or trash. Counting asks
only for UIDs, `BODYSTRUCTURE` and `INTERNALDATE`: no message is downloaded. A password is only
ever passed to `login`; nothing here logs.
"""

from __future__ import annotations

import base64
import contextlib
import imaplib
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from graphme.adapters.sources.mail_store import FolderState, MailStore

IMAP_PRESETS: dict[str, tuple[str, int]] = {
    "naver": ("imap.naver.com", 993),
    "daum": ("imap.daum.net", 993),
    "naverworks": ("imap.worksmobile.com", 993),
    "zoho": ("imap.zoho.com", 993),
    "gmail": ("imap.gmail.com", 993),
}
"""DaouOffice and Hiworks take the company's own host."""

_SKIPPED_FLAGS = frozenset({"\\junk", "\\trash", "\\drafts", "\\noselect"})
_SKIPPED_NAMES = frozenset({
    "spam", "junk", "trash", "deleted items", "drafts", "스팸메일함", "휴지통", "임시보관함"})
_SAVE_EVERY = 50
_BATCH = 200
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailRefused(Exception):
    """The server said no (a login, a command); the message is its words."""


class MailUnreachable(MailRefused):
    """The server could not be reached or dropped the connection (or timed out). It is a
    MailRefused so a caller that only reports "no" still catches it."""


def _failure(error: Exception) -> MailRefused:
    if isinstance(error, OSError | imaplib.IMAP4.abort):
        return MailUnreachable(f"connection lost or unreachable: {_words(error)}")
    return MailRefused(_words(error))


@dataclass(frozen=True)
class MailCount:
    messages: int
    attachments: int
    attachment_bytes: int | None
    """None where the service tells no size without fetching each message (Gmail)."""
    oldest: date | None


@dataclass(frozen=True)
class SyncResult:
    added: list[Path]
    removed: list[Path]


def imap_utf7_decode(name: str) -> str:
    """A folder name from modified UTF-7 (RFC 3501 5.1.3): `&AMQ-` is UTF-16 in base64."""
    def run(found: re.Match[str]) -> str:
        if not found[1]:
            return "&"
        text = found[1].replace(",", "/")
        return base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", run, name)


def _words(error: Exception) -> str:
    return " ".join(each.decode("utf-8", "replace") if isinstance(each, bytes) else str(each)
                    for each in error.args)


def _quoted(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_date(day: date) -> str:  # not strftime: the month must stay English
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


_LISTED = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.*)', re.DOTALL)


def _listed(item: bytes | tuple[bytes, bytes] | None) -> tuple[str, set[str]] | None:
    """`(flags, raw name)` from one LIST line; the name is a quoted string, an atom or a
    literal (then imaplib hands the line and the name as a pair)."""
    line, literal = item if isinstance(item, tuple) else (item, None)
    found = _LISTED.match(line or b"")
    if found is None:
        return None
    name = literal if literal is not None else found["name"].strip()
    if literal is None and name.startswith(b'"'):
        name = re.sub(rb"\\(.)", rb"\1", name[1:-1])
    flags = {each.lower() for each in found["flags"].decode().split()}
    return name.decode("utf-8", "replace"), flags


def _sexp(text: str) -> list[object]:
    """A parenthesised IMAP list as nested Python lists; NIL is None, atoms and strings str."""
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+', text)
    stack: list[list[object]] = [[]]
    for token in tokens:
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) == 1:  # the FETCH's own closing parenthesis: the list is over
                break
            done = stack.pop()
            stack[-1].append(done)
        elif token.upper() == "NIL":
            stack[-1].append(None)
        elif token.startswith('"'):
            stack[-1].append(re.sub(r"\\(.)", r"\1", token[1:-1]))
        else:
            stack[-1].append(token)
    return stack[0]


def _attachments(structure: list[object]) -> list[int]:
    """The size of each attachment in a parsed BODYSTRUCTURE. Declared attachments count; a
    part with a name and no disposition does too; an inline part (a logo) does not."""
    if structure and isinstance(structure[0], list):  # multipart: parts, then the subtype
        return [size for each in structure if isinstance(each, list) for size in _attachments(each)]
    kind = str(structure[0]).lower()
    subtype = str(structure[1]).lower() if len(structure) > 1 else ""
    after = 7 + (1 if kind == "text" else 3 if (kind, subtype) == ("message", "rfc822") else 0)
    disposition = structure[after + 1] if len(structure) > after + 1 else None
    declared = str(disposition[0]).lower() if isinstance(disposition, list) and disposition else ""
    params = structure[2]
    named = isinstance(params, list) and any(str(each).lower() == "name" for each in params[::2])
    if declared == "attachment" or (not declared and named and kind != "text"):
        size = structure[6] if len(structure) > 6 else "0"
        return [int(size) if isinstance(size, str) and size.isdigit() else 0]
    return []


class ImapMailbox:
    def __init__(
        self, host: str, port: int, address: str, password: str, *,
        connect: Callable[..., imaplib.IMAP4] = imaplib.IMAP4_SSL, timeout: int = 30,
    ) -> None:
        self._host, self._port, self._address = host, port, address
        self._password = password
        self._connect = connect
        self._timeout = timeout

    def _open(self) -> imaplib.IMAP4:
        try:
            connection = self._connect(self._host, self._port, timeout=self._timeout)
            try:
                connection.login(self._address, self._password)
            except BaseException:
                self._close(connection)
                raise
        except (imaplib.IMAP4.error, OSError) as error:
            raise _failure(error) from None
        return connection

    @staticmethod
    def _close(connection: imaplib.IMAP4) -> None:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            connection.logout()

    def check(self) -> None:
        self._close(self._open())

    def _listing(self, connection: imaplib.IMAP4) -> list[tuple[str, str]]:
        """`(name, raw name)` of each folder worth reading; the raw name is what the server
        is asked for, the name is what people read (and what the store keys by). A `\\All`
        folder is the only one: every other folder's mail is in it already."""
        _, data = connection.list()
        found: list[tuple[str, str]] = []
        for item in data:
            listed = _listed(item)
            if listed is None:
                continue
            raw, flags = listed
            name = imap_utf7_decode(raw)
            if "\\all" in flags and "\\noselect" not in flags:
                return [(name, raw)]
            if flags & _SKIPPED_FLAGS or re.split(r"[/.]", name)[-1].lower() in _SKIPPED_NAMES:
                continue
            found.append((name, raw))
        return found

    def folders(self) -> list[str]:
        connection = self._open()
        try:
            return [name for name, _ in self._listing(connection)]
        finally:
            self._close(connection)

    @staticmethod
    def _select(connection: imaplib.IMAP4, raw: str) -> int | None:
        """Open a folder read-only; its UIDVALIDITY, or None when it cannot be opened."""
        status, _ = connection.select(_quoted(raw), readonly=True)
        if status != "OK":
            return None
        _, valid = connection.response("UIDVALIDITY")
        return int(valid[0]) if valid and valid[0] else 0

    @staticmethod
    def _search(connection: imaplib.IMAP4, since: date | None) -> list[int]:
        # NOT DRAFT: a draft has no folder of its own in All Mail, and one never sent is not
        # mail anyone received; elsewhere the drafts folder is already left out.
        criteria = ("SINCE", _imap_date(since), "NOT", "DRAFT") if since else ("NOT", "DRAFT")
        _, data = connection.uid("SEARCH", None, *criteria)  # type: ignore[arg-type]
        return sorted(int(each) for each in (data[0] or b"").split())

    def count(self, since: date | None = None) -> MailCount:
        connection = self._open()
        messages = attachments = size = 0
        oldest: date | None = None
        try:
            for _, raw in self._listing(connection):
                if self._select(connection, raw) is None:
                    continue
                uids = self._search(connection, since)
                messages += len(uids)
                for at in range(0, len(uids), _BATCH):
                    chunk = ",".join(str(each) for each in uids[at:at + _BATCH])
                    _, data = connection.uid("FETCH", chunk, "(BODYSTRUCTURE INTERNALDATE)")
                    for item in data:
                        line = (item[0] if isinstance(item, tuple) else item) or b""
                        text = line.decode("utf-8", "replace")
                        stamp = imaplib.Internaldate2tuple(line)
                        if stamp is not None:
                            day = date(stamp.tm_year, stamp.tm_mon, stamp.tm_mday)
                            oldest = day if oldest is None else min(oldest, day)
                        at_structure = text.find("BODYSTRUCTURE ")
                        if at_structure < 0:
                            continue
                        try:
                            [parsed, *_] = _sexp(text[at_structure + len("BODYSTRUCTURE "):])
                            sizes = _attachments(parsed) if isinstance(parsed, list) else []
                        except (IndexError, ValueError):  # a structure we cannot read
                            continue
                        attachments += len(sizes)
                        size += sum(sizes)
        except (imaplib.IMAP4.error, OSError) as error:
            raise _failure(error) from None
        finally:
            self._close(connection)
        return MailCount(messages, attachments, size, oldest)

    def sync(
        self, store: MailStore, *, since: date | None = None,
        stop: threading.Event | None = None,
    ) -> SyncResult:
        """Bring the store level with the mailbox. A drop raises MailUnreachable (a refusal,
        MailRefused) with every file written whole and the state listing exactly those."""
        added: list[Path] = []
        removed: list[Path] = []
        connection = self._open()
        try:
            for name, raw in self._listing(connection):
                valid = self._select(connection, raw)
                if valid is None:
                    continue
                state = store.state(name)
                if state.uidvalidity != valid:
                    # The server renumbered the folder. What was read under the old numbers is
                    # set aside with its files, and each message seen again is held by its new
                    # UID; nothing is dropped until the folder has been listed again in full.
                    store.park(name)
                    state = FolderState(valid)
                    store.save_state(name, state)
                present = set(self._search(connection, None))
                removed += [p for uid in sorted(state.uids - present)
                            if (p := store.forget(name, uid))]
                state.uids &= present
                wanted = set(self._search(connection, since) if since else present)
                try:
                    for done, uid in enumerate(sorted(wanted - state.uids, reverse=True), 1):
                        if stop is not None and stop.is_set():
                            return self._result(added, removed)
                        _, data = connection.uid("FETCH", str(uid), "(BODY.PEEK[])")
                        body = next((each[1] for each in data if isinstance(each, tuple)), None)
                        if body is None:  # deleted since the search
                            continue
                        if (path := store.put(body, folder=name, uid=uid)) is not None:
                            added.append(path)
                        state.uids.add(uid)
                        if done % _SAVE_EVERY == 0:
                            store.save_state(name, state)
                finally:
                    # Also after a drop: what was written is recorded, so the files and the
                    # state agree and the next sync goes on from here.
                    store.save_state(name, state)
                if since is None and store.parked(name):
                    removed += store.settle(name)
        except (imaplib.IMAP4.error, OSError) as error:
            raise _failure(error) from None
        finally:
            self._close(connection)
        return self._result(added, removed)

    @staticmethod
    def _result(added: list[Path], removed: list[Path]) -> SyncResult:
        return SyncResult(added, removed)

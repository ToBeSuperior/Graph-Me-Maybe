"""One mail account's messages kept as `.eml` files, and which folder UIDs hold each.

A message is one file named by its subject -- the name the app shows for it -- and a digest of
its Message-ID (or of its bytes when it has none, as the extractor keys it) written as an
attachment's is (`shown_name` hides it), so the same message under two folders -- Gmail's
labels are folders over IMAP -- is kept once, and two of one subject apart. The name follows
from the message alone: nothing has to be kept to find it again. `state.json` says, per folder,
its UIDVALIDITY and the UIDs read, and
which (folder, UID) pairs hold each message, so a message goes only when no folder holds it.
Every write goes to a temp file beside its target and replaces it: a crash leaves the old file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from email.header import Header
from email.parser import BytesHeaderParser
from email.policy import default
from pathlib import Path

from graphme.adapters.extractors.mail import FOLDER_HEADER, message_key, without_folder_stamp
from graphme.application.embedded_documents import name_part, safe_name
from graphme.domain.ids import sha256_bytes

_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                       *(f"LPT{n}" for n in range(1, 10))})


@dataclass
class FolderState:
    uidvalidity: int = 0
    uids: set[int] = field(default_factory=set)


def _write(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".", suffix=".tmp", delete=False) as temp:
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            Path(temp.name).unlink(missing_ok=True)
            raise
    os.replace(temp.name, path)


def _named(raw: bytes, key: str) -> str:
    """The message's file name: its subject made fit for a file, then the digest that tells two
    messages of one subject apart, written as `embedded_name` writes an attachment's."""
    subject = str(BytesHeaderParser(policy=default).parsebytes(raw)["Subject"] or "")
    stem = safe_name(name_part(subject, "(no subject)") + ".eml")[:-len(".eml")]
    if stem.upper() in _RESERVED:  # Windows' device names, whatever follows the first dot
        stem = f"_{stem}"
    return f"{stem}.emb{key[:8]}.eml"


_OLD = "\0old"
"""Appended to a folder's name for what it held before the server renumbered it."""


class MailStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._messages = root / "messages"
        self._messages.mkdir(parents=True, exist_ok=True)
        self._file = root / "state.json"
        saved = json.loads(self._file.read_text(encoding="utf-8")) if self._file.exists() else {}
        self._folders: dict[str, FolderState] = {
            name: FolderState(each["uidvalidity"], set(each["uids"]))
            for name, each in saved.get("folders", {}).items()}
        # message file name -> the (folder, uid) pairs that hold it
        self._held: dict[str, set[tuple[str, int]]] = {
            name: {(folder, uid) for folder, uid in each}
            for name, each in saved.get("held", {}).items()}

    def put(self, raw: bytes, *, folder: str, uid: int) -> Path | None:
        """Keep a message. None when another folder already holds it (this one is noted)."""
        raw = without_folder_stamp(raw)
        key = sha256_bytes(message_key(raw).encode())[:32]
        # A store written before messages were named by subject keeps its digest names.
        path = self._messages / f"{key}.eml"
        if not path.exists():
            path = self._messages / _named(raw, key)
        holders = self._held.setdefault(path.name, set())
        # The same pair again (a retry after a drop) is not "another folder": it is reported.
        known = path.exists() and any(each != (folder, uid) for each in holders)
        holders.add((folder, uid))
        if known:
            return None
        if not path.exists():
            stamp = Header(folder, "utf-8", maxlinelen=10_000).encode()
            _write(path, f"{FOLDER_HEADER}: {stamp}\r\n".encode() + raw)
        return path

    def forget(self, folder: str, uid: int) -> Path | None:
        """Drop one folder's hold on a message; the file goes, and is returned, only when no
        folder holds it any more."""
        for name, holders in self._held.items():
            if (folder, uid) in holders:
                holders.discard((folder, uid))
                if holders:
                    return None
                del self._held[name]
                path = self._messages / name
                path.unlink(missing_ok=True)
                return path
        return None

    def state(self, folder: str) -> FolderState:
        saved = self._folders.get(folder)
        return FolderState(saved.uidvalidity, set(saved.uids)) if saved else FolderState()

    def park(self, folder: str) -> None:
        """The server renumbered the folder: what it held under the old UIDs is set aside, files
        kept, until the folder is listed again. A message seen again is held by its new UID
        (put finds the file there already); `settle` then drops what was not seen."""
        old = self._folders.pop(folder, None)
        if old is None:
            return
        # ponytail: a second renumber before settling merges two numberings; rare, and
        # the worst case is a stale hold kept, never a message dropped.
        parked = self._folders.setdefault(folder + _OLD, FolderState(old.uidvalidity))
        parked.uids |= old.uids
        for holders in self._held.values():
            for uid in [uid for name, uid in holders if name == folder]:
                holders.discard((folder, uid))
                holders.add((folder + _OLD, uid))

    def parked(self, folder: str) -> bool:
        return folder + _OLD in self._folders

    def settle(self, folder: str) -> list[Path]:
        """The folder is listed again in full: drop what only its old numbering held."""
        gone = [path for uid in sorted(self.state(folder + _OLD).uids)
                if (path := self.forget(folder + _OLD, uid))]
        self._folders.pop(folder + _OLD, None)
        self._save()
        return gone

    def save_state(self, folder: str, state: FolderState) -> None:
        self._folders[folder] = FolderState(state.uidvalidity, set(state.uids))
        self._save()

    def _save(self) -> None:
        document = {
            "folders": {name: {"uidvalidity": each.uidvalidity, "uids": sorted(each.uids)}
                        for name, each in self._folders.items()},
            "held": {name: sorted([folder, uid] for folder, uid in each)
                     for name, each in self._held.items() if each}}
        _write(self._file, json.dumps(document, ensure_ascii=False).encode("utf-8"))

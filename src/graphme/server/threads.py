"""Conversations kept on this computer: one JSON file a thread, `<id>.json`, in the threads
folder of the workspace it asks -- inside the app's home, never in a cloud drive's folder or a
browser's storage -- so deleting a workspace deletes its threads with its folder."""
from __future__ import annotations

import builtins
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from pydantic import ValidationError

from graphme.server.models import Thread, ThreadSummary, ThreadTurn

_log = logging.getLogger(__name__)

_ID = re.compile(r"[0-9a-f]{16}")
"""What `create` names a thread: anything else names no file here (a path never does)."""

_WRITING = threading.Lock()
"""Every change is read, changed and written under it: two turns at once both kept."""
# ponytail: one lock for every store; a lock a thread if conversations ever contend.


class ThreadStore:
    """The threads kept in `folder`."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def list(self) -> list[ThreadSummary]:
        """Every thread readable here, latest changed first; a file that is not one (corrupt,
        or of a shape this version does not read) is logged and left out."""
        kept: list[ThreadSummary] = []
        for path in self.folder.glob("*.json"):
            try:
                thread = Thread.model_validate_json(path.read_bytes())
            except (OSError, ValueError, ValidationError) as error:
                _log.warning("thread %s not read -- %s", path.name, str(error)[:200])
                continue
            if thread.id != path.stem:  # a copy under another name is not that thread
                _log.warning("thread %s names another thread (%s)", path.name, thread.id)
                continue
            kept.append(ThreadSummary(id=thread.id, title=thread.title,
                                      updated_at=thread.updated_at, turns=len(thread.turns)))
        return sorted(kept, key=lambda t: -t.updated_at)

    def has(self, thread_id: str) -> bool:
        return self._path(thread_id).is_file()

    def get(self, thread_id: str) -> Thread:
        """The thread; KeyError when there is none (or it cannot be read, or is another's)."""
        try:
            thread = Thread.model_validate_json(self._path(thread_id).read_bytes())
        except (OSError, ValueError, ValidationError) as error:
            raise KeyError(thread_id) from error
        if thread.id != thread_id:
            raise KeyError(thread_id)
        return thread

    def create(self, scope: builtins.list[str] | None, workspace: str) -> Thread:
        now = time.time()
        thread = Thread(id=secrets.token_hex(8), scope=scope, workspace=workspace,
                        created_at=now, updated_at=now)
        with _WRITING:
            self._write(thread)
        return thread

    def patch(self, thread_id: str, *, title: str | None = None,
              scope: builtins.list[str] | None = None, scope_given: bool = False) -> Thread:
        """The thread renamed, its scope replaced when `scope_given` (None: every document)."""
        with _WRITING:
            thread = self.get(thread_id)
            changed: dict[str, object] = {"updated_at": time.time()}
            if title is not None:
                changed["title"] = title
            if scope_given:
                changed["scope"] = scope
            thread = thread.model_copy(update=changed)
            self._write(thread)
        return thread

    def delete(self, thread_id: str) -> None:
        with _WRITING:
            try:
                self._path(thread_id).unlink()
            except FileNotFoundError as error:
                raise KeyError(thread_id) from error

    def append(self, thread_id: str, turn: ThreadTurn) -> Thread:
        """`turn` added after the others; a thread with no title takes the turn's question."""
        with _WRITING:
            thread = self.get(thread_id)
            thread = thread.model_copy(update={
                "turns": [*thread.turns, turn], "updated_at": time.time(),
                "title": thread.title or turn.question})
            self._write(thread)
        return thread

    def _path(self, thread_id: str) -> Path:
        if not _ID.fullmatch(thread_id):
            raise KeyError(thread_id)
        return self.folder / f"{thread_id}.json"

    def _write(self, thread: Thread) -> None:
        """Written whole beside the file, then put in its place: a crash leaves the old one."""
        self.folder.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(thread.model_dump_json())
                file.flush()
                os.fsync(file.fileno())  # on disk before it takes the old file's place
            os.replace(temporary, self._path(thread.id))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

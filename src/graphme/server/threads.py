"""Conversations kept on this computer: one JSON file a thread, `<id>.json`, in the threads
folder of the workspace it asks -- inside the app's home, never in a cloud drive's folder or a
browser's storage -- so deleting a workspace deletes its threads with its folder."""
from __future__ import annotations

import builtins
import contextlib
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from graphme.files import write_atomic
from graphme.server.models import (
    Answer,
    Found,
    Question,
    Thread,
    ThreadSummary,
    ThreadTurn,
    Turn,
    TurnRequest,
)

_log = logging.getLogger(__name__)

_ID = re.compile(r"[0-9a-f]{16}")
"""What `create` names a thread: anything else names no file here (a path never does)."""

_WRITING = threading.Lock()
"""Every change is read, changed and written under it: two turns at once both kept."""
# ponytail: one lock for every store; a lock a thread if conversations ever contend.


def question_of(thread: Thread, body: TurnRequest) -> Question:
    """A turn's question as it is asked: after the thread's answered turns, about its scope,
    in its workspace."""
    return Question(
        question=body.question, documents=body.documents, workspace=thread.workspace,
        only=thread.scope, exclude=body.exclude,
        history=[Turn(question=t.question, answer=t.answer.answer)
                 for t in thread.turns if t.answer is not None])


class ThreadStore:
    """The threads kept in `folder`."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def list(self) -> list[ThreadSummary]:
        """Every thread readable here, latest changed first; a file that is not one (corrupt,
        or of a shape this version does not read) is logged and left out. Its turns are
        counted, not read: `get` reads them."""
        kept: list[ThreadSummary] = []
        for path in self.folder.glob("*.json"):
            try:
                said = json.loads(path.read_bytes())
                thread = ThreadSummary(id=said["id"], title=said.get("title", ""),
                                       updated_at=said["updated_at"],
                                       turns=len(said.get("turns", [])))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                _log.warning("thread %s not read -- %s", path.name, str(error)[:200])
                continue
            if thread.id != path.stem:  # a copy under another name is not that thread
                _log.warning("thread %s names another thread (%s)", path.name, thread.id)
                continue
            kept.append(thread)
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

    def keep_turn(self, thread_id: str, made: ThreadTurn, got: Mapping[str, Any]) -> bool:
        """`made` with what the turn got (`found`, `answer`) added to the thread; False when
        the thread was deleted meanwhile. An answer cut off before its check is kept
        unchecked."""
        if "found" in got:
            made.found = Found.model_validate(got["found"])
        if "answer" in got:
            made.answer = Answer.model_validate({
                **(made.found.model_dump(include={"queries", "text_only"})
                   if made.found else {}), **got["answer"]})
        try:
            self.append(thread_id, made)
        except KeyError:
            return False
        return True

    def rescoped(self, thread: Thread, exclude: builtins.list[str] | None,
                 current: Callable[[frozenset[str]], Mapping[str, str]],
                 ) -> builtins.list[str] | None:
        """The thread's scope as the graph holds it now (`current`: each id to the one the
        graph holds it under): a document edited and built again since it was chosen is named
        by its new reading, and the thread keeps that."""
        if not thread.scope:
            return thread.scope
        now = current(frozenset(thread.scope) | frozenset(exclude or ()))
        scope = list(dict.fromkeys(now.get(i, i) for i in thread.scope))
        if scope != thread.scope:
            with contextlib.suppress(KeyError):  # deleted meanwhile: the turn says so
                self.patch(thread.id, scope=scope, scope_given=True)
        return scope

    def _path(self, thread_id: str) -> Path:
        if not _ID.fullmatch(thread_id):
            raise KeyError(thread_id)
        return self.folder / f"{thread_id}.json"

    def _write(self, thread: Thread) -> None:
        """Written whole beside the file, then put in its place: a crash leaves the old one."""
        write_atomic(self._path(thread.id), thread.model_dump_json())

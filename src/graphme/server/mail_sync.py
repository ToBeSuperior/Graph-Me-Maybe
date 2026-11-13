"""New mail read as it arrives: every few minutes each account is brought level with its
mailbox (`MailAccounts.sync`), what came is built into its workspace like a first read, and
what the mailbox no longer holds leaves the graph.

An account whose first-connect question is unanswered (`counted`) is not read. What cannot be
built yet -- the workspace is closed, or a build runs -- is held on the account and taken when
the workspace is open and idle (`opened` is called when a workspace is switched to). What is
built is what the store holds and the graph does not, not only what just came: a first read
cancelled, failed or closed half way after its fetch is built by the next pass.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from graphme.server.jobs import BUILDING, Busy, CorpusBuild
from graphme.server.mail_accounts import MailAccounts

if TYPE_CHECKING:
    from graphme.server.state import Demo

MAIL_CHECK_SECONDS = 300
MAIL_BACKOFF_MAX_SECONDS = 3600.0


def _wait(stop: threading.Event, seconds: float) -> None:
    stop.wait(seconds)


class MailSync:
    def __init__(
        self, accounts: MailAccounts, demo: Demo, *, every: float = MAIL_CHECK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[threading.Event, float], None] = _wait,
    ) -> None:
        self._accounts = accounts
        self._demo = demo
        self._every = every
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._started: dict[str, tuple[CorpusBuild, list[str]]] = {}
        """account -> the build started for its held files, and those files."""
        self._passing = threading.Lock()

    def run(self, stop: threading.Event) -> None:
        self._stop = stop  # a long IMAP sync is interrupted by it too
        while not stop.is_set():  # a pass at once: mail that came while the app was closed
            self.tick()
            self._sleep(stop, self._every)

    def tick(self) -> None:
        """One pass over the accounts. Not while any build runs: skipped, not queued."""
        with self._passing:
            for item in self._accounts.listed():
                if self._building() or self._stop.is_set():
                    return
                account = item["id"]
                if item["state"] == "counted" or self._clock() < self._retry_at.get(account, 0):
                    continue
                try:
                    result = self._accounts.sync(account, self._stop)  # keeps its failure
                except Exception:  # noqa: BLE001 -- this account waits longer; the others go on
                    self._back_off(account)
                    continue
                self._failures.pop(account, None)
                self._retry_at.pop(account, None)
                self.hold(account, result.added, result.removed)
                if self._stop.is_set():  # stopped half way: read on at the next start
                    self._accounts.set_state(account, "current")
                    return
                self._take(account)

    def opened(self, workspace: str) -> None:
        """`workspace` was switched to: what it was holding is taken, behind -- opening embeds
        what it holds first, a build can only start after that."""
        threading.Thread(target=self._take_held, args=(workspace,), daemon=True).start()

    def _take_held(self, workspace: str) -> None:
        self._demo._wait_for_embed()
        with self._passing:
            for item in self._accounts.listed():
                if item["workspace"] == workspace:
                    self._take(item["id"])

    def _building(self) -> bool:
        return self._demo.build.state in BUILDING

    def _back_off(self, account: str) -> None:
        failures = self._failures[account] = self._failures.get(account, 0) + 1
        wait = min(self._every * 2 ** (failures - 1) * 2, MAIL_BACKOFF_MAX_SECONDS)
        self._retry_at[account] = self._clock() + wait

    def hold(self, account: str, added: list[Path], removed: list[Path]) -> None:
        """What a sync added and removed, held for `_take`. A file added again cancels its
        removal still held, and the reverse: the mailbox's last word on a path is the one
        kept."""
        item = self._accounts.get(account)
        added_, removed_ = set(map(str, added)), set(map(str, removed))
        gone = ({*item.get("gone", []), *removed_}) - added_
        pending = ({*item.get("pending", []), *added_}) - removed_
        self._accounts.hold(account, sorted(pending), sorted(gone))

    def _take(self, account: str) -> None:
        """What the account holds, built and deleted if its workspace is open and idle."""
        if self._building():
            return
        item = self._accounts.get(account)
        if started := self._started.pop(account, None):
            build, files = started
            if build.state == "done":  # only now are those files taken; a failed build's stay
                item["pending"] = [p for p in item.get("pending", []) if p not in files]
                self._accounts.hold(account, item["pending"], item.get("gone", []))
        pending, gone = item.get("pending", []), item.get("gone", [])
        if item["state"] == "counted":  # the first-connect question comes first
            return
        if self._demo.workspace_registry().active != item["workspace"]:
            return
        try:
            if gone:
                self._delete([Path(p) for p in gone])
                self._accounts.hold(account, pending, [])
            files = self._missing(account)
            if files:
                docs = [self._demo.open_path(p) for p in files]
                self._started[account] = (
                    self._demo.build_corpus(docs, item["address"]), files)
            elif pending:
                self._accounts.hold(account, [], [])  # in the graph, or gone from disk
        except Busy:
            pass  # a build began since the check: the next pass
        except Exception as error:  # noqa: BLE001 -- held for the next pass, said on the account
            traceback.print_exc(file=sys.stderr)
            self._accounts.fail(account, str(error))

    def _missing(self, account: str) -> list[str]:
        """The account's stored messages whose content the graph does not hold (the build's
        own check, by content hash). The held files are among them until built."""
        stored = {self._demo.open_path(str(p)): str(p)
                  for p in sorted(self._accounts.messages(account).glob("*.eml"))}
        held = set(self._demo.already_in(list(stored)))
        return [path for doc, path in stored.items() if doc not in held]

    def _delete(self, paths: list[Path]) -> None:
        """The documents of these message files deleted, and each attachment that no other
        message still carries -- found before the delete takes the links away."""
        space = self._demo.space()
        messages = {d for p in paths for d in space.read_from(str(p))}
        if not messages:
            return
        ids = sorted(messages | space.carried_only_by(sorted(messages)))
        self._demo.delete_documents(ids)

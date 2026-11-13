"""A Gmail mailbox read through the Gmail API into a `MailStore`: read-only, newest first,
resumable -- the same three things `ImapMailbox` does, so the rest of mail takes either.

Gmail has labels, not folders, and every message once: the whole mailbox is listed as one
folder (`FOLDER`), spam, trash, drafts and chats left out by the query. A message's id is a
64-bit number written in hex, kept as the store's UID. Counting lists ids only; a message is
fetched (`format=raw`, the same MIME an IMAP fetch gives) only when it is read. The access token
comes from the caller and is never logged.
"""
from __future__ import annotations

import base64
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from graphme.adapters.sources.imap_mail import MailCount, MailRefused, MailUnreachable, SyncResult
from graphme.adapters.sources.mail_store import FolderState, MailStore

API = "https://gmail.googleapis.com/gmail/v1/users/me"
FOLDER = "Gmail"
QUERY = "-in:spam -in:trash -in:drafts -in:chats"
_SAVE_EVERY = 50
_PAGE = 500


class GmailMailbox:
    def __init__(self, http: httpx.Client, token: Callable[[], str]) -> None:
        self._http = http
        self._token = token

    def _get(self, path: str, **params: Any) -> dict[str, Any] | None:
        """Gmail's answer; None when the message is gone. Busy or unreachable is
        MailUnreachable (waited out and asked again), any other refusal MailRefused."""
        try:
            response = self._http.get(f"{API}/{path}", params=params, timeout=60,
                                      headers={"Authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as error:
            raise MailUnreachable(f"Gmail could not be reached: {error}") from None
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise MailUnreachable(f"Gmail is busy ({response.status_code}) -- read on later")
        if response.status_code != 200:
            try:
                said = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                said = response.text[:200]
            raise MailRefused(f"Gmail refused ({response.status_code}): {said}")
        found: dict[str, Any] = response.json()
        return found

    def address(self) -> str:
        return str((self._get("profile") or {})["emailAddress"])

    def check(self) -> None:
        self.address()

    def _ids(self, since: date | None, *words: str) -> Iterator[str]:
        """Every message id the query finds, newest first."""
        # `after:` is read in Google's own time zone: a day earlier, so none of `since` is lost.
        query = " ".join([QUERY, *words, *(
            [f"after:{(since - timedelta(days=1)):%Y/%m/%d}"] if since else [])])
        page: str | None = None
        while True:
            found = self._get("messages", q=query, maxResults=_PAGE,
                              **({"pageToken": page} if page else {})) or {}
            yield from (str(m["id"]) for m in found.get("messages", []))
            page = found.get("nextPageToken")
            if not page:
                return

    def count(self, since: date | None = None) -> MailCount:
        """How many messages, how many carry an attachment, and the oldest one's day. Gmail
        tells no size without fetching each message, so the attachments' size is not said."""
        ids = list(self._ids(since))
        attached = sum(1 for _ in self._ids(since, "has:attachment"))
        oldest = None
        if ids and (last := self._get(f"messages/{ids[-1]}", format="minimal")):
            oldest = datetime.fromtimestamp(int(last["internalDate"]) / 1000, UTC).date()
        return MailCount(len(ids), attached, None, oldest)

    def sync(self, store: MailStore, *, since: date | None = None,
             stop: threading.Event | None = None) -> SyncResult:
        """Bring the store level with the mailbox: what it no longer holds goes, what it holds
        from `since` on is fetched, newest first. Every file is written whole and the state
        lists exactly those, also when stopped or refused half way."""
        added: list[Path] = []
        state = store.state(FOLDER)
        if state.uidvalidity != 1:
            state = FolderState(1)
        present = list(self._ids(None))
        held = {int(i, 16) for i in present}
        removed = [p for uid in sorted(state.uids - held) if (p := store.forget(FOLDER, uid))]
        state.uids &= held
        wanted = list(self._ids(since)) if since else present
        try:
            for done, ident in enumerate((i for i in wanted if int(i, 16) not in state.uids), 1):
                if stop is not None and stop.is_set():
                    break
                found = self._get(f"messages/{ident}", format="raw")
                if found is None:  # deleted since it was listed
                    continue
                raw = base64.urlsafe_b64decode(found["raw"] + "=" * (-len(found["raw"]) % 4))
                if (path := store.put(raw, folder=FOLDER, uid=int(ident, 16))) is not None:
                    added.append(path)
                state.uids.add(int(ident, 16))
                if done % _SAVE_EVERY == 0:
                    store.save_state(FOLDER, state)
        finally:
            store.save_state(FOLDER, state)
        return SyncResult(added, removed)

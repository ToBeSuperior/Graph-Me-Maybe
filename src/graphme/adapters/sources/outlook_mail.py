"""An Outlook mailbox -- Outlook.com or Microsoft 365 -- read through Microsoft Graph into a
`MailStore`: read-only, newest first, resumable, the same three things `ImapMailbox` and
`GmailMailbox` do.

Graph lists every message of the mailbox at once (`/me/messages`); what sits in Junk Email or
Deleted Items, and drafts, are left out. Ids are asked for as immutable ones, so a message moved
between folders keeps its id; each is kept in the store under a number made from it. Counting
lists ids only; a message is fetched as MIME (`$value`, what an IMAP fetch gives) only when it is
read. The access token comes from the caller and is never logged.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from graphme.adapters.sources.imap_mail import MailCount, MailRefused, MailUnreachable, SyncResult
from graphme.adapters.sources.mail_store import FolderState, MailStore
from graphme.domain.ids import sha256_bytes

API = "https://graph.microsoft.com/v1.0"
FOLDER = "Outlook"
_LEFT_OUT = ("junkemail", "deleteditems")
"""Well-known folders whose mail is not read."""
_SAVE_EVERY = 50
_PAGE = 500


def _uid(ident: str) -> int:
    """A Graph id is a long string; the store keeps a number. 60 bits of its digest."""
    return int(sha256_bytes(ident.encode())[:15], 16)


class OutlookMailbox:
    def __init__(self, http: httpx.Client, token: Callable[[], str]) -> None:
        self._http = http
        self._token = token

    def _get(self, url: str, **params: Any) -> httpx.Response | None:
        """Graph's answer; None when the message is gone. Busy or unreachable is
        MailUnreachable (waited out and asked again), any other refusal MailRefused."""
        try:
            response = self._http.get(
                url if url.startswith("http") else f"{API}{url}", params=params or None,
                timeout=60, headers={"Authorization": f"Bearer {self._token()}",
                                     "Prefer": 'IdType="ImmutableId"'})
        except httpx.HTTPError as error:
            raise MailUnreachable(f"Outlook could not be reached: {error}") from None
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise MailUnreachable(f"Outlook is busy ({response.status_code}) -- read on later")
        if response.status_code != 200:
            try:
                said = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                said = response.text[:200]
            raise MailRefused(f"Outlook refused ({response.status_code}): {said}")
        return response

    def _json(self, url: str, **params: Any) -> dict[str, Any]:
        found = self._get(url, **params)
        answer: dict[str, Any] = found.json() if found is not None else {}
        return answer

    def address(self) -> str:
        me = self._json("/me", **{"$select": "mail,userPrincipalName"})
        return str(me.get("mail") or me["userPrincipalName"])

    def check(self) -> None:
        self.address()

    def _rows(self, since: date | None) -> Iterator[dict[str, Any]]:
        """Every message from `since` on (all, when None), newest first, but those in Junk
        Email and Deleted Items and drafts."""
        skipped = {str(self._json(f"/me/mailFolders/{name}", **{"$select": "id"}).get("id"))
                   for name in _LEFT_OUT}
        url: str | None = "/me/messages"
        params: dict[str, Any] = {
            "$select": "id,receivedDateTime,parentFolderId,hasAttachments",
            # The property sorted on comes first in the filter, as Graph asks.
            "$filter": f"receivedDateTime ge {(since or date(1900, 1, 1)).isoformat()}T00:00:00Z"
                       " and isDraft eq false",
            "$orderby": "receivedDateTime desc", "$top": _PAGE}
        while url:
            page = self._json(url, **params)
            yield from (row for row in page.get("value", [])
                        if row.get("parentFolderId") not in skipped)
            url, params = page.get("@odata.nextLink"), {}

    def count(self, since: date | None = None) -> MailCount:
        """How many messages, how many carry an attachment, and the oldest one's day. Graph
        tells no attachments' size without fetching each message: it is not said."""
        rows = list(self._rows(since))
        oldest = None
        if rows:
            oldest = datetime.fromisoformat(rows[-1]["receivedDateTime"].replace("Z", "+00:00"))
        return MailCount(len(rows), sum(1 for r in rows if r.get("hasAttachments")), None,
                         oldest.date() if oldest else None)

    def sync(self, store: MailStore, *, since: date | None = None,
             stop: threading.Event | None = None) -> SyncResult:
        """Bring the store level with the mailbox: what it no longer holds (or holds in Junk
        Email or Deleted Items) goes, what it holds from `since` on is fetched, newest first.
        Every file is written whole and the state lists exactly those, also when stopped or
        refused half way."""
        added: list[Path] = []
        state = store.state(FOLDER)
        if state.uidvalidity != 1:
            state = FolderState(1)
        present = [str(r["id"]) for r in self._rows(None)]
        held = {_uid(i) for i in present}
        removed = [p for uid in sorted(state.uids - held) if (p := store.forget(FOLDER, uid))]
        state.uids &= held
        wanted = [str(r["id"]) for r in self._rows(since)] if since else present
        try:
            for done, ident in enumerate((i for i in wanted if _uid(i) not in state.uids), 1):
                if stop is not None and stop.is_set():
                    break
                found = self._get(f"/me/messages/{ident}/$value")
                if found is None:  # deleted since it was listed
                    continue
                if (path := store.put(found.content, folder=FOLDER, uid=_uid(ident))) is not None:
                    added.append(path)
                state.uids.add(_uid(ident))
                if done % _SAVE_EVERY == 0:
                    store.save_state(FOLDER, state)
        finally:
            store.save_state(FOLDER, state)
        return SyncResult(added, removed)

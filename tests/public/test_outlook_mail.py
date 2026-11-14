"""An Outlook mailbox read through Microsoft Graph into a `MailStore`, against a fake Graph:
read-only, newest first, junk, deleted items and drafts left out. Invented mail."""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from graphme.adapters.sources.imap_mail import MailRefused, MailUnreachable
from graphme.adapters.sources.mail_store import MailStore
from graphme.adapters.sources.outlook_mail import API, OutlookMailbox


def _raw(n: int) -> bytes:
    return (f"Message-ID: <o{n}@invented.test>\r\nFrom: lee@invented.test\r\n"
            f"Subject: Invented {n}\r\nContent-Type: text/plain\r\n\r\nBody {n}.\r\n").encode()


class _Graph:
    """Graph's mail API as far as reading uses it: messages newest first, their folder, and a
    page at a time."""

    def __init__(self) -> None:
        # id -> (day received, folder, has attachments, draft)
        self.mail: dict[str, tuple[date, str, bool, bool]] = {
            "AAMk-4": (date(2026, 10, 4), "inbox", True, False),
            "AAMk-3": (date(2026, 10, 3), "junk", False, False),
            "AAMk-2": (date(2026, 9, 2), "sent", False, False),
            "AAMk-5": (date(2026, 9, 1), "drafts", False, True),
            "AAMk-6": (date(2026, 8, 1), "deleted", False, False),
            "AAMk-1": (date(2026, 1, 1), "inbox", False, False),
        }
        self.fetched: list[str] = []
        self.status = 200
        self.page = 2

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer invented-access"
        assert "ImmutableId" in request.headers.get("prefer", "")  # ids that survive a move
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "invented refusal"}})
        path = request.url.path.removeprefix("/v1.0")
        if path == "/me":
            return httpx.Response(200, json={"mail": None,
                                             "userPrincipalName": "kim@invented.test"})
        if path.startswith("/me/mailFolders/"):
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1].replace("junkemail",
                                                                                    "junk")
                                             .replace("deleteditems", "deleted")})
        if path == "/me/messages":
            return self._list(request.url.params)
        ident = path.split("/")[3]
        if ident not in self.mail:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        self.fetched.append(ident)
        return httpx.Response(200, content=_raw(int(ident.rsplit("-", 1)[1])))

    def _list(self, params: httpx.QueryParams) -> httpx.Response:
        assert params["$orderby"] == "receivedDateTime desc"
        words = params["$filter"]
        assert "isDraft eq false" in words
        after = date.fromisoformat(words.split("receivedDateTime ge ")[1][:10])
        rows = [{"id": i, "receivedDateTime": f"{day.isoformat()}T09:00:00Z",
                 "parentFolderId": folder, "hasAttachments": attached}
                for i, (day, folder, attached, draft) in self.mail.items()
                if day >= after and not draft]
        start = int(params.get("$skip", 0))
        page: dict[str, object] = {"value": rows[start:start + self.page]}
        if start + self.page < len(rows):  # the link carries the whole query, as Graph's does
            asked = params.set("$skip", str(start + self.page))
            page["@odata.nextLink"] = f"{API}/me/messages?{asked}"
        return httpx.Response(200, json=page)


def _mailbox(graph: _Graph) -> OutlookMailbox:
    return OutlookMailbox(httpx.Client(transport=httpx.MockTransport(graph)),
                          lambda: "invented-access")


def test_the_address_is_the_signed_in_account() -> None:
    assert _mailbox(_Graph()).address() == "kim@invented.test"


def test_counting_reads_no_message() -> None:
    graph = _Graph()

    counted = _mailbox(graph).count()

    assert (counted.messages, counted.attachments, counted.oldest) == (3, 1, date(2026, 1, 1))
    assert counted.attachment_bytes is None and graph.fetched == []


def test_a_sync_reads_newest_first_and_leaves_junk_deleted_and_drafts(tmp_path: Path) -> None:
    graph = _Graph()
    store = MailStore(tmp_path)

    result = _mailbox(graph).sync(store)

    assert graph.fetched == ["AAMk-4", "AAMk-2", "AAMk-1"]
    assert len(result.added) == 3 and result.removed == []
    assert _mailbox(graph).sync(store).added == [] and len(graph.fetched) == 3


def test_since_reads_only_recent_mail_and_a_later_sync_reads_the_rest(tmp_path: Path) -> None:
    graph = _Graph()
    store = MailStore(tmp_path)

    _mailbox(graph).sync(store, since=date(2026, 9, 1))
    recent = list(graph.fetched)
    _mailbox(graph).sync(store)

    assert recent == ["AAMk-4", "AAMk-2"] and graph.fetched[2:] == ["AAMk-1"]


def test_mail_gone_from_the_mailbox_leaves_the_store(tmp_path: Path) -> None:
    graph = _Graph()
    store = MailStore(tmp_path)
    first = _mailbox(graph).sync(store)

    graph.mail["AAMk-2"] = (date(2026, 9, 2), "deleted", False, False)  # moved to Deleted Items
    result = _mailbox(graph).sync(store)

    assert len(result.removed) == 1 and not result.removed[0].exists()
    assert result.removed[0] in first.added


def test_a_stop_leaves_what_was_read_kept_and_goes_on_next_time(tmp_path: Path) -> None:
    graph = _Graph()
    store = MailStore(tmp_path)
    stop = threading.Event()
    stop.set()

    assert _mailbox(graph).sync(store, stop=stop).added == []
    assert len(_mailbox(graph).sync(store).added) == 3


@pytest.mark.parametrize(("status", "error"), [
    (401, MailRefused), (403, MailRefused), (429, MailUnreachable), (503, MailUnreachable)])
def test_graph_refusing_is_said_and_a_busy_graph_is_waited_for(
    tmp_path: Path, status: int, error: type[Exception]
) -> None:
    graph = _Graph()
    graph.status = status

    with pytest.raises(error) as raised:
        _mailbox(graph).sync(MailStore(tmp_path))
    assert status in (429, 503) or "invented refusal" in str(raised.value)

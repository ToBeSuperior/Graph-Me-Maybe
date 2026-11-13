"""A Gmail mailbox read through the Gmail API into a `MailStore`, against a fake Gmail:
read-only, newest first, spam, trash and drafts left out. Invented mail."""
from __future__ import annotations

import base64
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from graphme.adapters.sources.gmail_mail import API, GmailMailbox
from graphme.adapters.sources.imap_mail import MailRefused, MailUnreachable
from graphme.adapters.sources.mail_store import MailStore


def _raw(n: int, *, attached: bool = False) -> bytes:
    head = (f"Message-ID: <m{n}@invented.test>\r\nFrom: kim@invented.test\r\n"
            f"Subject: Invented {n}\r\n")
    if not attached:
        return (head + "Content-Type: text/plain\r\n\r\nBody of invented mail.\r\n").encode()
    return (head + 'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\n'
            "Content-Type: text/plain\r\n\r\nSee attached.\r\n--b\r\n"
            'Content-Type: application/pdf\r\nContent-Disposition: attachment; filename="a.pdf"'
            "\r\n\r\n%PDF-1.4\r\n--b--\r\n").encode()


class _Gmail:
    """Gmail's API as far as reading uses it. Messages are newest first, as Gmail lists them;
    `q` is honoured for the words reading sends."""

    def __init__(self) -> None:
        # id -> (day received, labels, attached)
        self.mail: dict[str, tuple[date, set[str], bool]] = {
            "18c0000000000004": (date(2026, 10, 4), {"INBOX"}, True),
            "18c0000000000003": (date(2026, 10, 3), {"SPAM"}, False),
            "18c0000000000002": (date(2026, 9, 2), {"SENT"}, False),
            "18c0000000000001": (date(2026, 1, 1), {"INBOX"}, False),
        }
        self.drafts = {"18c0000000000005"}
        self.fetched: list[str] = []
        self.status = 200
        self.page = 2  # small pages, so paging is used

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer invented-access"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "invented refusal"}})
        path = str(request.url.copy_with(query=None))
        if path == f"{API}/profile":
            return httpx.Response(200, json={"emailAddress": "kim@invented.test"})
        if path == f"{API}/messages":
            return self._list(request.url.params)
        found = self.mail.get(path.rsplit("/", 1)[1])
        if found is None:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        day, _, attached = found
        ident = path.rsplit("/", 1)[1]
        if request.url.params["format"] == "minimal":
            stamp = int(date.toordinal(day) - date(1970, 1, 1).toordinal()) * 86_400_000
            return httpx.Response(200, json={"id": ident, "internalDate": str(stamp)})
        self.fetched.append(ident)
        raw = base64.urlsafe_b64encode(_raw(int(ident, 16) % 100, attached=attached))
        return httpx.Response(200, json={"id": ident, "raw": raw.decode()})

    def _list(self, params: httpx.QueryParams) -> httpx.Response:
        words = params["q"].split()
        assert {"-in:spam", "-in:trash", "-in:drafts"} <= set(words)
        after = next((w[6:] for w in words if w.startswith("after:")), None)
        ids = [i for i, (day, labels, attached) in self.mail.items()
               if "SPAM" not in labels and i not in self.drafts
               and (after is None or day > date(*map(int, after.split("/"))))
               and ("has:attachment" not in words or attached)]
        start = int(params.get("pageToken", 0))
        page = {"messages": [{"id": i, "threadId": i} for i in ids[start:start + self.page]]}
        if start + self.page < len(ids):
            page["nextPageToken"] = str(start + self.page)
        return httpx.Response(200, json=page)


def _mailbox(gmail: _Gmail) -> GmailMailbox:
    return GmailMailbox(httpx.Client(transport=httpx.MockTransport(gmail)),
                        lambda: "invented-access")


def test_the_address_is_the_signed_in_account() -> None:
    assert _mailbox(_Gmail()).address() == "kim@invented.test"


def test_counting_reads_no_message() -> None:
    gmail = _Gmail()

    counted = _mailbox(gmail).count()

    assert (counted.messages, counted.attachments, counted.oldest) == (3, 1, date(2026, 1, 1))
    assert counted.attachment_bytes is None  # Gmail says no size without fetching each one
    assert gmail.fetched == []


def test_a_sync_reads_newest_first_and_leaves_spam_and_drafts(tmp_path: Path) -> None:
    gmail = _Gmail()
    store = MailStore(tmp_path)

    result = _mailbox(gmail).sync(store)

    assert gmail.fetched == ["18c0000000000004", "18c0000000000002", "18c0000000000001"]
    assert len(result.added) == 3 and result.removed == []
    assert _mailbox(gmail).sync(store).added == []  # nothing new: nothing fetched again
    assert len(gmail.fetched) == 3


def test_since_reads_only_recent_mail_and_a_later_sync_reads_the_rest(tmp_path: Path) -> None:
    gmail = _Gmail()
    store = MailStore(tmp_path)

    _mailbox(gmail).sync(store, since=date(2026, 9, 1))
    recent = list(gmail.fetched)
    _mailbox(gmail).sync(store)

    assert recent == ["18c0000000000004", "18c0000000000002"]
    assert gmail.fetched[2:] == ["18c0000000000001"]


def test_mail_gone_from_the_mailbox_leaves_the_store(tmp_path: Path) -> None:
    gmail = _Gmail()
    store = MailStore(tmp_path)
    first = _mailbox(gmail).sync(store)

    del gmail.mail["18c0000000000002"]
    result = _mailbox(gmail).sync(store)

    assert len(result.removed) == 1 and not result.removed[0].exists()
    assert result.removed[0] in first.added


def test_a_stop_leaves_what_was_read_kept_and_goes_on_next_time(tmp_path: Path) -> None:
    gmail = _Gmail()
    store = MailStore(tmp_path)
    stop = threading.Event()
    stop.set()

    assert _mailbox(gmail).sync(store, stop=stop).added == []
    assert len(_mailbox(gmail).sync(store).added) == 3


@pytest.mark.parametrize(("status", "error"), [
    (401, MailRefused), (403, MailRefused), (429, MailUnreachable), (503, MailUnreachable)])
def test_gmail_refusing_is_said_and_a_busy_gmail_is_waited_for(
    tmp_path: Path, status: int, error: type[Exception]
) -> None:
    gmail = _Gmail()
    gmail.status = status

    with pytest.raises(error) as raised:
        _mailbox(gmail).sync(MailStore(tmp_path))
    assert status in (429, 503) or "invented refusal" in str(raised.value)

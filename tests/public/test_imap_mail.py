"""The IMAP mailbox against a fake server: what it reads, what it leaves, and what a failure
leaves behind. Invented messages."""

from __future__ import annotations

import imaplib
import json
import threading
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import pytest

from graphme.adapters.sources.imap_mail import (
    ImapMailbox,
    MailRefused,
    MailUnreachable,
    imap_utf7_decode,
)
from graphme.adapters.sources.mail_store import MailStore
from tests.fakes.imap import FakeFolder, FakeImap


def _raw(identifier: str, *, attachment: bool = False) -> bytes:
    message = EmailMessage()
    message["From"] = "Kim <kim@invented.test>"
    message["Subject"] = "견적"
    message["Message-ID"] = f"<{identifier}@invented.test>"
    message.set_content("본문입니다.")
    if attachment:
        message.add_attachment(b"x" * 10, maintype="application", subtype="octet-stream",
                               filename="견적서.xlsx")
    return message.as_bytes()


def _mailbox(fake: FakeImap) -> ImapMailbox:
    return ImapMailbox("imap.invented.test", 993, "kim@invented.test", "app-password",
                       connect=fake)


def _fake(**folders: list[str]) -> FakeImap:
    fake = FakeImap({name: FakeFolder() for name in folders})
    for name, identifiers in folders.items():
        for each in identifiers:
            fake.add(name, _raw(each))
    return fake


def _names(paths: list[Path]) -> int:
    return len(paths)


def test_utf7_folder_names_are_decoded() -> None:
    assert imap_utf7_decode("&vPStAA-") == "보관"
    assert imap_utf7_decode("A &- B") == "A & B"


def test_spam_trash_and_drafts_are_not_read() -> None:
    fake = FakeImap({
        "INBOX": FakeFolder(), "Junk": FakeFolder(),  # no flags: by name
        "Spam": FakeFolder(), "휴지통": FakeFolder(),
        "Bin": FakeFolder(flags="\\Trash"), "Notes": FakeFolder(flags="\\Drafts"),
        "Work": FakeFolder(flags="\\HasNoChildren"),
    })
    assert _mailbox(fake).folders() == ["INBOX", "Work"]


def _gmail() -> FakeImap:
    return FakeImap({
        "INBOX": FakeFolder(), "[Gmail]/Sent Mail": FakeFolder(flags="\\Sent"),
        "[Gmail]/All Mail": FakeFolder(flags="\\All"), "[Gmail]/Spam": FakeFolder(flags="\\Junk"),
    })


def test_a_server_with_all_mail_is_read_from_that_folder_alone(tmp_path: Path) -> None:
    fake = _gmail()
    fake.add("INBOX", _raw("in-inbox"))
    fake.add("[Gmail]/All Mail", _raw("in-inbox"))
    fake.add("[Gmail]/All Mail", _raw("archived"))  # no label left: only All Mail holds it
    fake.add("[Gmail]/All Mail", _raw("unsent"), draft=True)
    box = _mailbox(fake)
    assert box.folders() == ["[Gmail]/All Mail"]
    store = MailStore(tmp_path)
    assert len(box.sync(store).added) == 2
    assert store.state("[Gmail]/All Mail").uids == {1, 2}  # the draft is not read
    selected = {c[1] for c in fake.calls if c[0] == "select"}
    assert selected == {'"[Gmail]/All Mail"'}
    assert box.count().messages == 2


def test_a_message_in_two_folders_is_kept_once(tmp_path: Path) -> None:
    fake = _fake(INBOX=["same"], Work=["same", "other"])
    store = MailStore(tmp_path)
    result = _mailbox(fake).sync(store)
    assert _names(result.added) == 2
    assert len(list((tmp_path / "messages").glob("*.eml"))) == 2


def test_new_mail_is_added_and_deleted_mail_removed(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a", "b"])
    store = MailStore(tmp_path)
    _mailbox(fake).sync(store)
    del fake.folders["INBOX"].messages[1]
    fake.add("INBOX", _raw("c"))
    result = _mailbox(fake).sync(store)
    assert len(result.added) == 1 and len(result.removed) == 1
    assert not result.removed[0].exists() and result.added[0].exists()
    assert store.state("INBOX").uids == {2, 3}


def _files(root: Path) -> set[str]:
    return {p.name for p in (root / "messages").glob("*.eml")}


def test_a_renumbered_folder_is_listed_again_without_duplicates(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a", "b", "gone"])
    store = MailStore(tmp_path)
    first = _mailbox(fake).sync(store)
    fake.renumber("INBOX")
    del fake.folders["INBOX"].messages[max(fake.folders["INBOX"].messages)]  # "gone" is deleted
    again = _mailbox(fake).sync(store)
    assert again.added == []  # kept files are not read in again
    assert len(again.removed) == 1 and not again.removed[0].exists()
    assert _files(tmp_path) == {p.name for p in first.added} - {again.removed[0].name}
    assert store.state("INBOX").uids == set(fake.folders["INBOX"].messages)
    assert store.state("INBOX").uidvalidity == 2
    assert not store.parked("INBOX")


def test_a_renumbered_folder_keeps_its_files_when_the_connection_drops(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a", "b", "c"])
    store = MailStore(tmp_path)
    _mailbox(fake).sync(store)
    before = _files(tmp_path)
    fake.renumber("INBOX")
    fake.fetched, fake.fail_fetch_after = 0, 1
    with pytest.raises(MailUnreachable):
        _mailbox(fake).sync(store)
    assert _files(tmp_path) == before
    fake.fail_fetch_after = None
    result = _mailbox(fake).sync(store)
    assert result.removed == [] and _files(tmp_path) == before
    assert store.state("INBOX").uids == set(fake.folders["INBOX"].messages)
    assert not store.parked("INBOX")


def test_a_renumbered_folder_read_with_since_keeps_older_mail(tmp_path: Path) -> None:
    fake = FakeImap({"INBOX": FakeFolder()})
    fake.add("INBOX", _raw("old"), day=date(2025, 1, 1))
    fake.add("INBOX", _raw("new"), day=date(2026, 6, 1))
    store = MailStore(tmp_path)
    _mailbox(fake).sync(store)
    before = _files(tmp_path)
    fake.renumber("INBOX")
    assert _mailbox(fake).sync(store, since=date(2026, 1, 1)).removed == []
    assert _files(tmp_path) == before and store.parked("INBOX")
    assert _mailbox(fake).sync(store).removed == []  # a full pass settles it
    assert _files(tmp_path) == before and not store.parked("INBOX")


def test_newest_first_and_resumed_after_a_stop(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a", "b", "c", "d", "e"])
    store = MailStore(tmp_path)
    stop = threading.Event()
    real = fake.uid

    def watching(command: str, *args: object) -> tuple[str, list[object]]:
        answer = real(command, *args)
        if fake.fetched == 3:
            stop.set()
        return answer

    fake.uid = watching  # type: ignore[method-assign]
    _mailbox(fake).sync(store, stop=stop)
    assert store.state("INBOX").uids == {5, 4, 3}  # newest first
    fake.uid = real  # type: ignore[method-assign]
    fake.calls.clear()
    result = _mailbox(fake).sync(store)
    fetched = [c[2] for c in fake.calls if c[:2] == ("uid", "FETCH")]
    assert fetched == ["2", "1"] and len(result.added) == 2


def test_since_leaves_older_mail_for_later(tmp_path: Path) -> None:
    fake = FakeImap({"INBOX": FakeFolder()})
    fake.add("INBOX", _raw("old"), day=date(2025, 1, 1))
    fake.add("INBOX", _raw("new"), day=date(2026, 6, 1))
    store = MailStore(tmp_path)
    box = _mailbox(fake)
    assert len(box.sync(store, since=date(2026, 1, 1)).added) == 1
    assert store.state("INBOX").uids == {2}
    # the older mail is still on the server: it is not mistaken for deleted
    assert box.sync(store, since=date(2026, 1, 1)).removed == []
    assert len(box.sync(store).added) == 1


def test_count_downloads_nothing() -> None:
    fake = FakeImap({"INBOX": FakeFolder()})
    fake.add("INBOX", _raw("a", attachment=True), day=date(2026, 2, 3))
    fake.add("INBOX", _raw("b"), day=date(2026, 5, 6))
    count = _mailbox(fake).count()
    assert (count.messages, count.attachments, count.attachment_bytes) == (2, 1, 10)
    assert count.oldest == date(2026, 2, 3)
    assert not any("BODY.PEEK[]" in str(call) for call in fake.calls)
    assert _mailbox(fake).count(date(2026, 4, 1)).messages == 1


def test_a_refused_login_or_dropped_connection_changes_nothing(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a", "b", "c"])
    fake.login_error = "AUTHENTICATIONFAILED"
    with pytest.raises(MailRefused, match="AUTHENTICATIONFAILED"):
        _mailbox(fake).check()
    store = MailStore(tmp_path)
    with pytest.raises(MailRefused, match="AUTHENTICATIONFAILED"):
        _mailbox(fake).sync(store)
    assert not any("app-password" in str(call) for call in fake.calls)
    fake.login_error = None
    fake.fail_fetch_after = 2
    with pytest.raises(MailUnreachable, match="dropped"):
        _mailbox(fake).sync(store)
    # the two messages written before the drop are listed, so files and state agree
    saved = json.loads((tmp_path / "state.json").read_text("utf-8"))
    assert saved["folders"]["INBOX"]["uids"] == [2, 3]
    assert len(_files(tmp_path)) == 2
    assert sum(len(each) for each in saved["held"].values()) == 2
    assert not [p for p in tmp_path.rglob("*") if p.suffix == ".tmp"]
    fake.fail_fetch_after = None
    assert len(_mailbox(fake).sync(store).added) == 1  # goes on from there


def test_an_aborted_connection_is_reported_like_a_dropped_one(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a"])
    fake.fail_fetch_error = imaplib.IMAP4.abort("socket error: EOF")
    with pytest.raises(MailUnreachable, match="EOF"):
        _mailbox(fake).sync(MailStore(tmp_path))
    fake.login_error = "AUTHENTICATIONFAILED"
    with pytest.raises(MailRefused) as refused:
        _mailbox(fake).check()
    assert not isinstance(refused.value, MailUnreachable)


def test_folders_are_opened_read_only(tmp_path: Path) -> None:
    fake = _fake(INBOX=["a"], Work=["b"])
    _mailbox(fake).sync(MailStore(tmp_path))
    _mailbox(fake).count()
    selects = [call for call in fake.calls if call[0] == "select"]
    assert selects and all(call[2] is True for call in selects)

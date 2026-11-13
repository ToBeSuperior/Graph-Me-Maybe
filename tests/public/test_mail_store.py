"""The account's mail store: one file per message, state beside it. Invented messages."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from graphme.adapters.extractors.mail import MailExtractor, message_key
from graphme.adapters.sources.mail_store import FolderState, MailStore
from graphme.application.embedded_documents import shown_name
from graphme.domain.ids import sha256_bytes


def _raw(identifier: str | None, body: str = "견적을 보냅니다.", subject: str = "견적") -> bytes:
    message = EmailMessage()
    message["From"] = "Kim <kim@invented.test>"
    if subject:
        message["Subject"] = subject
    if identifier:
        message["Message-ID"] = identifier
    message.set_content(body)
    return message.as_bytes()


def test_put_names_the_file_by_message_id_and_stamps_the_folder(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail")
    path = store.put(_raw("<a@invented.test>"), folder="받은편지함", uid=7)
    assert path is not None and path.suffix == ".eml"
    document = MailExtractor().extract(path).document.metadata
    assert document["mail_folder"] == "받은편지함"
    assert document["mail_message_id"] == "<a@invented.test>"


def test_a_message_without_message_id_is_keyed_as_the_extractor_keys_it(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail")
    raw = _raw(None)
    first = store.put(raw, folder="A", uid=1)
    assert first is not None
    assert store.put(raw, folder="B", uid=9) is None  # same bytes, other folder: one message
    key = MailExtractor().extract(first).document.metadata["mail_message_id"]
    assert str(key).startswith("sha256:")


def test_forget_keeps_a_message_another_folder_holds(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail")
    raw = _raw("<a@invented.test>")
    path = store.put(raw, folder="A", uid=1)
    store.put(raw, folder="B", uid=5)
    assert store.forget("A", 1) is None and path is not None and path.exists()
    assert store.forget("B", 5) == path and not path.exists()


def test_state_survives_a_reopen_and_leaves_no_temp_files(tmp_path: Path) -> None:
    root = tmp_path / "mail"
    store = MailStore(root)
    path = store.put(_raw("<a@invented.test>"), folder="A", uid=1)
    store.save_state("A", FolderState(42, {1}))
    again = MailStore(root)
    assert again.state("A") == FolderState(42, {1})
    assert again.state("never") == FolderState()
    assert again.forget("A", 1) == path  # who holds what is kept too
    assert not [p for p in root.rglob("*") if p.name.endswith(".tmp")]


def test_a_folder_header_the_server_sent_cannot_name_the_folder(tmp_path: Path) -> None:
    forged = b"X-GMM-Folder: Forged\r\n  continued\r\n" + _raw("<a@invented.test>")
    path = MailStore(tmp_path / "mail").put(forged, folder="INBOX", uid=1)
    assert path is not None
    assert MailExtractor().extract(path).document.metadata["mail_folder"] == "INBOX"
    assert path.read_bytes().lower().count(b"x-gmm-folder") == 1


def test_a_message_is_named_by_its_subject_and_two_alike_apart(tmp_path: Path) -> None:
    # The file's name is what the app shows for the message: its subject, not a digest.
    root = tmp_path / "mail"
    store = MailStore(root)
    first = store.put(_raw("<a@invented.test>", subject="Re: 견적 / 검토?"), folder="A", uid=1)
    second = store.put(_raw("<b@invented.test>", subject="Re: 견적 / 검토?"), folder="A", uid=2)
    untitled = store.put(_raw("<c@invented.test>", subject=""), folder="A", uid=3)

    assert first is not None and second is not None and untitled is not None
    # What is shown is the subject; the digest a name carries tells two of one subject apart.
    assert [shown_name(p.name) for p in (first, second, untitled)] == [
        "Re 견적 검토.eml", "Re 견적 검토.eml", "(no subject).eml"]
    assert first != second
    # The name follows from the message alone: the same message again, after a reopen with
    # nothing saved, is the same file, not kept twice.
    assert MailStore(root).put(_raw("<a@invented.test>", subject="Re: 견적 / 검토?"),
                               folder="B", uid=9) == first
    assert len(list((root / "messages").glob("*.eml"))) == 3


def test_a_store_named_by_digest_keeps_its_names(tmp_path: Path) -> None:
    root = tmp_path / "mail"
    raw = _raw("<a@invented.test>")
    old = root / "messages" / (sha256_bytes(message_key(raw).encode())[:32] + ".eml")
    old.parent.mkdir(parents=True)
    old.write_bytes(raw)

    assert MailStore(root).put(raw, folder="A", uid=1) == old
    assert len(list(old.parent.glob("*.eml"))) == 1


def test_a_forged_header_does_not_change_a_digest_key(tmp_path: Path) -> None:
    raw = _raw(None)
    store = MailStore(tmp_path / "mail")
    first = store.put(raw, folder="A", uid=1)
    assert first is not None
    assert store.put(b"x-gmm-folder: Forged\r\n" + raw, folder="B", uid=2) is None
    assert MailExtractor().extract(first).document.metadata["mail_message_id"] == \
        message_key(raw)

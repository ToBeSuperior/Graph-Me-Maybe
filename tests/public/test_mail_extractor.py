"""A mail message read as a document: headers as metadata, the body as text, nothing drawn."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from graphme.adapters.extractors.mail import MailExtractor
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.domain.enums import DocumentFormat, NodeType


def _write(tmp_path: Path, message: EmailMessage, name: str = "m.eml") -> Path:
    path = tmp_path / name
    path.write_bytes(message.as_bytes())
    return path


def _units(path: Path) -> list[tuple[NodeType, str]]:
    extracted = MailExtractor().extract(path)
    return [(n.type, n.text or "") for n in extracted.nodes if n.type is not NodeType.DOCUMENT]


def test_headers_become_the_documents_metadata(tmp_path: Path) -> None:
    message = EmailMessage()
    message["From"] = "Kim <kim@invented.test>"
    message["To"] = "a@invented.test, Lee <b@invented.test>"
    message["Subject"] = "견적 검토"
    message["Date"] = "Mon, 12 Oct 2026 09:30:00 +0900"
    message["Message-ID"] = "<one@invented.test>"
    message["X-GMM-Folder"] = "INBOX"
    message.set_content("본문")
    extracted = MailExtractor().extract(_write(tmp_path, message))
    meta = extracted.document.metadata
    assert meta["mail_from"] == "Kim <kim@invented.test>"
    assert meta["mail_subject"] == "견적 검토"
    assert meta["mail_date"] == "2026-10-12T09:30:00+09:00"
    assert meta["mail_to"] == ["a@invented.test", "Lee <b@invented.test>"]
    assert meta["mail_message_id"] == "<one@invented.test>"
    assert meta["mail_folder"] == "INBOX"
    assert extracted.nodes[0].metadata["mail_subject"] == "견적 검토"


def test_the_html_part_is_taken_and_its_table_kept(tmp_path: Path) -> None:
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("A")
    message.add_alternative(
        "<p>B</p><table><tr><td>품목</td><td>단가</td></tr>"
        "<tr><td>밸브</td><td>1,200</td></tr></table>", subtype="html")
    assert _units(_write(tmp_path, message)) == [
        (NodeType.TEXT_CHUNK, "B"), (NodeType.TABLE, "품목 | 단가\n밸브 | 1,200")]


def test_paragraphs_inside_a_table_cell_stay_apart(tmp_path: Path) -> None:
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("A")
    message.add_alternative(
        "<table><tr><td><p>밸브</p><p>DN50</p></td><td><div>1,200</div></td></tr></table>",
        subtype="html")
    assert _units(_write(tmp_path, message)) == [(NodeType.TABLE, "밸브 DN50 | 1,200")]


def test_a_table_laid_out_for_the_eye_is_read_as_its_paragraphs(tmp_path: Path) -> None:
    # Notification and newsletter mail lays its page out with tables inside tables, one cell a
    # row: that is the page's shape, not a table of values, and its paragraphs stay apart. A
    # table of values inside it is still a table.
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("A")
    message.add_alternative(
        "<table><tr><td><table>"
        "<tr><td><div>Security alert</div></td></tr>"
        "<tr><td><p>A new passkey was added.</p><p>If this was not you, check it.</p></td></tr>"
        "<tr><td><table><tr><td>품목</td><td>단가</td></tr>"
        "<tr><td>밸브</td><td>1,200</td></tr></table></td></tr>"
        "<tr><td>Line one<br>Line two</td></tr>"
        "</table></td></tr></table>", subtype="html")
    assert _units(_write(tmp_path, message)) == [
        (NodeType.TEXT_CHUNK, "Security alert"),
        (NodeType.TEXT_CHUNK, "A new passkey was added."),
        (NodeType.TEXT_CHUNK, "If this was not you, check it."),
        (NodeType.TABLE, "품목 | 단가\n밸브 | 1,200"),
        (NodeType.TEXT_CHUNK, "Line one\nLine two")]


def test_a_line_break_in_the_html_source_is_a_space_and_only_br_breaks_a_line(
    tmp_path: Path
) -> None:
    # The sender's HTML is wrapped for its editor, not its reader: a browser shows a newline in
    # the source as a space. A sentence wrapped around a link stayed broken into three lines.
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("A")
    message.add_alternative(
        "<p>\n  Signed in with\n  <span><a href='x'>GitHub</a></span>\n  (at 3:47)\n</p>"
        "<p>first line<br>\nsecond line</p><pre>kept\n  as written</pre>", subtype="html")
    assert [t for _, t in _units(_write(tmp_path, message))] == [
        "Signed in with GitHub (at 3:47)", "first line\nsecond line", "kept\nas written"]


def test_what_the_message_hides_from_its_reader_is_not_read(tmp_path: Path) -> None:
    # Mail carries text no reader sees -- the inbox's preview line, a copy for another screen
    # size -- hidden by its style; read, it doubled every paragraph.
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("A")
    message.add_alternative(
        '<div style="display: none; max-height:0">Preview line</div>'
        '<p>Shown</p><div style="DISPLAY:NONE"><div><p>Hidden copy</p></div></div>'
        "<p>Also shown</p>", subtype="html")
    assert [t for _, t in _units(_write(tmp_path, message))] == ["Shown", "Also shown"]


def test_a_plain_only_message_is_read_as_paragraphs(tmp_path: Path) -> None:
    message = EmailMessage()
    message.set_content("first one\n\nsecond one")
    assert [t for _, t in _units(_write(tmp_path, message))] == ["first one", "second one"]


def test_korean_in_legacy_charsets_survives(tmp_path: Path) -> None:
    subject = base64.b64encode("회의록".encode("cp949")).decode()
    body = base64.b64encode("안녕하세요".encode("euc-kr")).decode()
    raw = (
        f"Subject: =?ks_c_5601-1987?B?{subject}?=\r\nMIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=euc-kr\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\n{body}\r\n").encode()
    path = tmp_path / "k.eml"
    path.write_bytes(raw)
    extracted = MailExtractor().extract(path)
    assert extracted.document.metadata["mail_subject"] == "회의록"
    assert [t for _, t in _units(path)] == ["안녕하세요"]


def test_a_message_with_no_id_or_date_still_reads(tmp_path: Path) -> None:
    message = EmailMessage()
    message.set_content("x")
    path = _write(tmp_path, message)
    from graphme.domain.ids import sha256_bytes

    meta = MailExtractor().extract(path).document.metadata
    assert meta["mail_message_id"] == "sha256:" + sha256_bytes(path.read_bytes())[:32]
    assert "mail_date" not in meta


class _Refuses:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"an .eml must not be drawn or asked of a model: {name}")


def test_an_eml_is_built_without_drawing(tmp_path: Path) -> None:
    message = EmailMessage()
    message.set_content("one\n\ntwo")
    path = _write(tmp_path, message)
    builder = DocumentGraphBuilder(
        extractors={DocumentFormat.EML: MailExtractor()},
        visual_binder=_Refuses(),  # type: ignore[arg-type]
        hierarchy_provider=_Refuses(),  # type: ignore[arg-type]
        unit_grouper=_Refuses(),  # type: ignore[arg-type]
        enricher=_Refuses(),  # type: ignore[arg-type]
    )
    built = builder.build(path, DocumentFormat.EML)
    assert [n.text for n in built.nodes if n.type is NodeType.TEXT_CHUNK] == ["one", "two"]


def test_is_mail_tells_a_message_from_a_document(tmp_path: Path) -> None:
    from graphme.adapters.extractors.mail import is_mail

    message = EmailMessage()
    message.set_content("x")
    document = MailExtractor().extract(_write(tmp_path, message)).nodes[0]
    assert is_mail(document)


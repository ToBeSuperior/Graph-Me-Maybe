"""An attachment is read once and cited from each message that carried it; an answer says
which mail a passage came from. Invented messages."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from graphme.adapters.extractors.mail import MailExtractor
from graphme.adapters.storage.graph_rag import _item
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.embedded_documents import carried_in
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType

SHEET = b"invented workbook bytes"
SENDER = "Kim <kim@invented.test>"


def _mail(tmp_path: Path, name: str, *, inline: bool = False) -> Path:
    message = EmailMessage()
    message["From"] = SENDER
    message["Message-ID"] = f"<{name}@invented.test>"
    message.set_content("견적서를 첨부합니다.")
    message.add_attachment(SHEET, maintype="application", subtype="octet-stream",
                           filename="견적서.xlsx")
    if inline:
        message.add_attachment(b"\x89PNG invented", maintype="image", subtype="png",
                               disposition="inline", cid="<logo@invented.test>")
    path = tmp_path / f"{name}.eml"
    path.write_bytes(message.as_bytes())
    return path


def test_an_emls_attachments_are_carried(tmp_path: Path) -> None:
    [each] = carried_in(_mail(tmp_path, "a", inline=True))
    assert each.name.startswith("견적서.emb") and each.name.endswith(".xlsx")
    assert each.content == SHEET and each.owner == "" and each.readable


def test_the_same_attachment_in_two_messages_is_one_document(tmp_path: Path) -> None:
    [first] = carried_in(_mail(tmp_path, "a"))
    [second] = carried_in(_mail(tmp_path, "b"))
    assert first.name == second.name


def test_a_message_cites_its_attachment_without_being_enriched(tmp_path: Path) -> None:
    path = _mail(tmp_path, "a")
    built = DocumentGraphBuilder(extractors={DocumentFormat.EML: MailExtractor()}).build(
        path, DocumentFormat.EML)
    [cited] = [n for n in built.nodes if n.type is NodeType.EXTERNAL_DOCUMENT]
    assert cited.label.startswith("견적서.emb")
    assert any(e.type is EdgeType.REFERENCES and e.source_id == built.document.id
               and e.target_id == cited.id for e in built.edges)


def test_mail_sent_at_orders_by_time_not_by_the_date_string(tmp_path: Path) -> None:
    def sent_at(date: str) -> float:
        message = EmailMessage()
        message["Date"] = date
        message["Message-ID"] = "<t@invented.test>"
        message.set_content("x")
        path = tmp_path / "t.eml"
        path.write_bytes(message.as_bytes())
        return float(MailExtractor().extract(path).document.metadata["mail_sent_at"])  # type: ignore[arg-type]

    later = sent_at("Mon, 12 Oct 2026 23:30:00 -0500")  # 2026-10-13 04:30 UTC
    earlier = sent_at("Tue, 13 Oct 2026 09:00:00 +0900")  # 2026-10-13 00:00 UTC
    assert later > earlier  # as ISO strings it would be the other way round


def test_a_mail_without_a_date_or_sender_still_says_it_is_mail() -> None:
    item = _item(_record(mail_message_id="<x@invented.test>"))  # type: ignore[arg-type]
    assert "Mail from ?, subject: " in item.content and " on ," not in item.content
    item = _item(_record(carriers=[{"sender": SENDER, "date": None, "subject": "견적"}]))  # type: ignore[arg-type]
    assert "Attached to: 견적 (Kim <kim@invented.test>)" in item.content


def _record(**more: object) -> dict[str, object]:
    return {"id": "d:1", "score": 1.0, "document_id": "d", "file": "견적.eml", "text": "본문",
            "members": [], "linked": [], "modified": None, "opening": None, "pages": [],
            **more}


def test_a_mail_passage_says_who_sent_it() -> None:
    item = _item(_record(
        mail_message_id="<m@invented.test>", mail_from=SENDER,
        mail_date="2026-10-12T09:30:00+09:00", mail_subject="견적 검토"))  # type: ignore[arg-type]
    assert "Mail from Kim <kim@invented.test> on 2026-10-12, subject: 견적 검토" in item.content


def test_an_attachment_says_which_mail_carried_it() -> None:
    carriers = [
        {"sender": SENDER, "date": "2026-10-14T10:00:00+09:00", "subject": "Re: 견적 검토"},
        {"sender": SENDER, "date": "2026-10-12T09:30:00+09:00", "subject": "견적 검토"},
    ]
    item = _item(_record(carriers=carriers))  # type: ignore[arg-type]
    assert ("Attached to: Re: 견적 검토 (Kim <kim@invented.test>, 2026-10-14); "
            "견적 검토 (Kim <kim@invented.test>, 2026-10-12)") in item.content


def _attribute(level: int, ident: int, data: bytes) -> bytes:
    return (bytes([level]) + ident.to_bytes(4, "little") + len(data).to_bytes(4, "little")
            + data + (sum(data) % 65536).to_bytes(2, "little"))


def _winmail(files: list[tuple[str, bytes]]) -> bytes:
    """An invented TNEF stream as Outlook writes it: per file a start, a short title, the long
    name among its MAPI properties (PR_ATTACH_LONG_FILENAME, Unicode) and the bytes."""
    stream = (0x223E9F78).to_bytes(4, "little") + (1).to_bytes(2, "little")
    stream += _attribute(1, 0x00089006, (1).to_bytes(4, "little"))  # attTnefVersion
    for name, data in files:
        wide = name.encode("utf-16-le") + b"\0\0"
        props = ((1).to_bytes(4, "little") + (0x001F).to_bytes(2, "little")
                 + (0x3707).to_bytes(2, "little") + (1).to_bytes(4, "little")
                 + len(wide).to_bytes(4, "little") + wide + b"\0" * (-len(wide) % 4))
        stream += _attribute(2, 0x00069002, b"\0" * 14)  # attAttachRendData
        stream += _attribute(2, 0x00018010, b"FILE~1.XLS\0")  # attAttachTitle
        stream += _attribute(2, 0x0006800F, data)  # attAttachData
        stream += _attribute(2, 0x00069005, props)  # attAttachment
    return stream


def test_files_inside_outlooks_winmail_dat_are_carried(tmp_path: Path) -> None:
    message = EmailMessage()
    message["Message-ID"] = "<tnef@invented.test>"
    message.set_content("견적서를 첨부합니다.")
    message.add_attachment(_winmail([("견적서.xlsx", SHEET), ("메모.docx", b"invented memo")]),
                           maintype="application", subtype="ms-tnef", filename="winmail.dat")
    path = tmp_path / "tnef.eml"
    path.write_bytes(message.as_bytes())
    sheet, memo = carried_in(path)
    assert sheet.name.startswith("견적서.emb") and sheet.content == SHEET and sheet.readable
    assert memo.name.startswith("메모.emb") and memo.content == b"invented memo"

"""HTML and calendar parts that are legal but unusual must still be read. Invented mail."""
from __future__ import annotations

import threading
from email.message import EmailMessage
from pathlib import Path

from graphme.adapters.extractors.mail import MailExtractor
from graphme.domain.enums import NodeType


def _texts(tmp_path: Path, message: EmailMessage) -> str:
    path = tmp_path / "m.eml"
    path.write_bytes(message.as_bytes())
    extracted = MailExtractor().extract(path)
    return "\n".join(n.text or "" for n in extracted.nodes if n.type is not NodeType.DOCUMENT)


def _html(body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("plain")
    message.add_alternative(body, subtype="html")
    return message


def test_a_hidden_element_never_closed_inside_a_table_ends_the_reading(tmp_path: Path) -> None:
    message = _html('<table><tr><td>Pears<p style="display:none">preheader</td></tr></table>')
    done: list[str] = []
    reader = threading.Thread(target=lambda: done.append(_texts(tmp_path, message)),
                              daemon=True)
    reader.start()
    reader.join(timeout=10)
    assert done, "the reading never ended"
    assert "Pears" in done[0]


def test_a_head_left_open_does_not_hide_the_body(tmp_path: Path) -> None:
    message = _html('<html><head><meta charset="utf-8"><body><p>Plums ripen in June.</p>')
    assert "Plums ripen in June." in _texts(tmp_path, message)


def test_a_calendar_in_an_unknown_charset_is_still_read(tmp_path: Path) -> None:
    message = EmailMessage()
    message["Subject"] = "s"
    message.set_content("See the invite.")
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Orchard walk\r\n"
           "DTSTART:20310402T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    message.add_attachment(ics.encode(), maintype="text", subtype="calendar",
                           params={"charset": "x-invented"}, filename="invite.ics")
    assert "Orchard walk" in _texts(tmp_path, message)

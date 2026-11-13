"""A message through the server: read and put in the space like any document, never drawn and
never sent to a model. Invented message."""
from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.domain.enums import NodeType
from graphme.server import create_app, state
from graphme.server.state import Demo
from tests.public.server.conftest import FakeSpace, until


class _Refuses:
    """Wiring may take its methods; calling one is the failure."""

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"an .eml must not be drawn or asked of a model: {name}")

        return call


@pytest.fixture
def message(tmp_path: Path) -> Path:
    mail = EmailMessage()
    mail["Subject"] = "견적 검토"
    mail["Message-ID"] = "<m1@invented.test>"
    mail.set_content("first paragraph\n\nsecond paragraph")
    path = tmp_path / "quote.eml"
    path.write_bytes(mail.as_bytes())
    return path


@pytest.fixture
def guarded(demo: Demo, monkeypatch: pytest.MonkeyPatch) -> Demo:
    demo.renderer = _Refuses()  # type: ignore[assignment]
    monkeypatch.setattr(state, "create_vision", lambda *a, **k: _Refuses())
    return demo


def test_a_message_session_is_extracted_only_and_put_in_the_space(
    guarded: Demo, space: FakeSpace, message: Path
) -> None:
    doc = guarded.open_path(str(message))
    session = guarded.start(doc)
    assert until(lambda: session.state in ("done", "error")), session.state
    assert session.state == "done" and session.pages == ()
    assert session.extracted is not None
    assert [n.text for n in session.extracted.nodes if n.type is NodeType.TEXT_CHUNK] == [
        "first paragraph", "second paragraph"]

    build = guarded.build_corpus([doc], "test")
    assert until(lambda: build.state == "done"), build
    assert space.put == [session.extracted.document.id]


def test_a_message_has_no_pages_to_preview(
    guarded: Demo, message: Path
) -> None:
    doc = guarded.open_path(str(message))
    with pytest.raises(LookupError, match="no pages"):
        guarded.preview(doc, 1)
    client = TestClient(create_app(guarded), base_url="http://127.0.0.1:8765")
    assert client.get(f"/v1/files/{doc}/pages/1/image").status_code == 404

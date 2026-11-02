"""A document's original opened on this machine in its own app -- what an MCP client asks for
when the user wants the file itself."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from graphme.server.state import Demo
from tests.public.server.conftest import FakeSpace


def test_a_file_on_this_machine_is_opened_where_it_is(
    tmp_path: Path, demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    original = tmp_path / "Invented memo.docx"
    original.write_bytes(b"invented")
    space.paths["doc-1"] = str(original)
    opened: list[Path] = []
    demo.open_file = opened.append  # type: ignore[method-assign]

    said = client.post("/v1/documents/doc-1/open")

    assert said.status_code == 200 and said.json() == {"opened": "Invented memo.docx"}
    assert opened == [original]


def test_a_file_kept_only_in_a_cloud_drive_is_fetched_then_opened(
    tmp_path: Path, demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    fetched = tmp_path / "fetched.pdf"
    fetched.write_bytes(b"%PDF invented")

    @contextmanager
    def open_copy(origin: str) -> Any:
        assert origin == "box://123/Invented plan.pdf"
        yield fetched

    space.paths["doc-2"] = "box://123/Invented plan.pdf"
    demo.sources.open_copy = open_copy  # type: ignore[method-assign]
    opened: list[Path] = []
    demo.open_file = opened.append  # type: ignore[method-assign]

    said = client.post("/v1/documents/doc-2/open")

    assert said.status_code == 200 and said.json() == {"opened": "Invented plan.pdf"}
    assert len(opened) == 1 and opened[0].name == "Invented plan.pdf"
    assert opened[0].read_bytes() == b"%PDF invented"  # a copy of its own, kept to be read


def test_a_document_whose_file_is_gone_or_unknown_is_404(
    tmp_path: Path, demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    space.paths["doc-3"] = str(tmp_path / "moved away.xlsx")
    demo.open_file = lambda path: None  # type: ignore[method-assign]

    assert client.post("/v1/documents/doc-3/open").status_code == 404
    assert client.post("/v1/documents/no-such/open").status_code == 404


def test_only_this_machine_opens_a_file(
    tmp_path: Path, demo: Demo, space: FakeSpace, client: TestClient
) -> None:
    original = tmp_path / "Invented memo.docx"
    original.write_bytes(b"invented")
    space.paths["doc-1"] = str(original)
    demo.open_file = lambda path: None  # type: ignore[method-assign]

    said = client.post("/v1/documents/doc-1/open", headers={"origin": "https://example.com"})

    assert said.status_code == 403

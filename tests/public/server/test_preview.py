"""A document is shown by where its build drew each node, and only the page looked at is drawn,
when it is looked at: no page image is kept for every document. Invented PDF."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz
from fastapi.testclient import TestClient

from graphme.adapters.export.neo4j import graph_records
from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.adapters.rendering.visual_binding import with_geometry
from graphme.domain.enums import DocumentFormat
from graphme.server import create_app
from graphme.server.state import Demo, Session
from tests.public.server.conftest import SETTINGS, FakeSpace


class _Counted(PdfPageRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.drawn: list[str] = []

    def render(self, source: Path, document_format: DocumentFormat) -> Any:
        self.drawn.append("all")
        return super().render(source, document_format)

    def preview(self, source: Path, document_format: DocumentFormat, index: int) -> bytes:
        self.drawn.append(f"page {index}")
        return super().preview(source, document_format, index)


def _built(tmp_path: Path) -> tuple[Path, Any]:
    source = tmp_path / "invented.pdf"
    document = fitz.open()
    for number in (1, 2):
        document.new_page().insert_text((72, 72), f"Invented page {number}")
    document.save(source)
    extracted = PDFExtractor().extract(source)
    return source, with_geometry(extracted, PdfPageRenderer().render(source, DocumentFormat.PDF),
                                 source)


def test_a_built_document_opens_with_no_drawing_but_of_the_page_looked_at(tmp_path: Path) -> None:
    source, built = _built(tmp_path)
    space = FakeSpace()
    document_id = built.document.id
    space.held[document_id], space.paths[document_id] = source.name, str(source)
    space.document_graph = lambda _id: graph_records(built)  # type: ignore[method-assign]
    counted = _Counted()
    demo = Demo(SETTINGS, renderer=counted, root=tmp_path / "server")  # type: ignore[arg-type]
    demo.graph._space = space  # type: ignore[assignment]
    client = TestClient(create_app(demo), base_url="http://127.0.0.1:8765")

    doc = client.post(f"/v1/documents/{document_id}/file").json()["file_id"]
    pages = client.get(f"/v1/files/{doc}/pages").json()
    boxes = client.get(f"/v1/files/{doc}/boxes").json()["boxes"]

    assert [sheet["index"] for sheet in pages["sheets"]] == [1, 2] and not pages["pending"]
    assert boxes and {box["page"] for box in boxes.values()} == {1, 2}
    assert counted.drawn == []  # nothing drawn, nothing extracted, to know where things are

    first = client.get(f"/v1/files/{doc}/pages/2/image")
    again = client.get(f"/v1/files/{doc}/pages/2/image")
    assert first.status_code == again.status_code == 200
    assert first.content.startswith(b"\x89PNG") and counted.drawn == ["page 2"]

    source.unlink()  # its pages and boxes need no file; a page not drawn yet does
    assert client.get(f"/v1/files/{doc}/pages").status_code == 200
    assert client.get(f"/v1/files/{doc}/pages/1/image").status_code == 404


def test_a_reading_leaves_no_page_image_behind(tmp_path: Path) -> None:
    workspace = tmp_path / "doc"
    workspace.mkdir()
    (workspace / "page-01.png").write_bytes(b"\x89PNG invented")
    from graphme.adapters.rendering.browser_office import RenderedPage

    session = Session(source=tmp_path / "invented.pdf", workspace=workspace,
                      format=DocumentFormat.PDF,
                      pages=(RenderedPage(index=1, width=1, height=1, png=b"\x89PNG"),))

    Demo._lighten(session)

    assert not (workspace / "page-01.png").exists() and session.pages[0].png == b""


def test_what_earlier_versions_kept_of_every_page_is_cleared(tmp_path: Path) -> None:
    root = tmp_path / "server"
    kept = root / "abc"
    kept.mkdir(parents=True)
    for name in ("page-01.png", "pages.json", "bound.json", "notes.txt"):
        (kept / name).write_text("invented")

    Demo(SETTINGS, renderer=None, root=root)  # type: ignore[arg-type]

    assert sorted(path.name for path in kept.iterdir()) == ["notes.txt"]

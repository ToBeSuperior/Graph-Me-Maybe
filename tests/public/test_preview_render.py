"""A page drawn for a reader is that page alone, at a preview's resolution. Invented documents."""
from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
from PIL import Image

from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer, RenderedPage
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.domain.enums import DocumentFormat


def _pdf(path: Path, pages: int) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        document.new_page().insert_text((72, 72), f"Invented page {number}")
    document.save(path)
    return path


def test_a_pdf_preview_is_one_page_at_fewer_pixels_than_its_reading(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "invented.pdf", 3)
    read = PdfPageRenderer().render(source, DocumentFormat.PDF)

    shown = Image.open(io.BytesIO(PdfPageRenderer().preview(source, DocumentFormat.PDF, 2)))

    assert shown.width < read[1].width and shown.height < read[1].height
    assert abs(shown.width / shown.height - read[1].width / read[1].height) < 0.01


def test_a_browser_preview_asks_for_its_page_alone_at_preview_resolution(tmp_path: Path) -> None:
    asked: list[dict[str, float]] = []

    def capture(source: Path, kind: str, workspace: Path) -> list[RenderedPage]:
        asked.append(json.loads((workspace / "view.json").read_text(encoding="utf-8")))
        return [RenderedPage(index=3, width=10, height=10, png=b"\x89PNG invented")]

    source = tmp_path / "invented.pptx"
    source.write_bytes(b"invented")
    renderer = BrowserOfficeRenderer(viewer_directory=tmp_path, capture=capture)

    assert renderer.preview(source, DocumentFormat.PPTX, 3) == b"\x89PNG invented"
    assert asked == [{"only": 2, "dpr": 1.0}]


def test_a_built_document_carries_where_each_node_is_drawn(tmp_path: Path) -> None:
    # A preview found its pages and boxes by drawing and binding the document again; the build
    # had them already, and now writes them on the graph that goes into the space.
    from graphme.adapters.extractors.pdf import PDFExtractor
    from graphme.adapters.rendering.page_renderer import PageRenderer
    from graphme.adapters.rendering.visual_binding import RenderedVisualBinder
    from graphme.adapters.storage.assets import LocalAssetStore
    from graphme.application.document_graph import DocumentGraphBuilder
    from graphme.domain.enums import NodeType

    source = _pdf(tmp_path / "invented.pdf", 2)
    renderer = PageRenderer(browser=PdfPageRenderer(), pdf=PdfPageRenderer())
    builder = DocumentGraphBuilder(
        extractors={DocumentFormat.PDF: PDFExtractor()},
        visual_binder=RenderedVisualBinder(renderer=renderer,
                                           asset_store=LocalAssetStore(tmp_path / "assets")))

    built = builder.build(source, DocumentFormat.PDF)

    root = next(node for node in built.nodes if node.type is NodeType.DOCUMENT)
    assert [page["index"] for page in root.metadata["pages"]] == [1, 2]  # type: ignore[index,union-attr,call-overload]
    texts = [node for node in built.nodes if node.type is NodeType.TEXT_CHUNK]
    assert texts and all(node.metadata.get("page") in (1, 2) and len(node.metadata["box"]) == 4
                         for node in texts)

"""A PDF table that runs over a page is one table: the second piece is held by the first.

A PDF states each page apart, so a table broken by the page end is two tables in the file.
They are one when the first is the last thing on its page and the second the first thing on
the next -- only what recurs on every page (a running header, a page number) between them --
and they are drawn on the same grid of columns.
"""
from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.adapters.rendering.visual_binding import join_vertically
from graphme.application.visual_crops import CROP_ASSET_KEY, join_continued
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    SourceLocator,
)

COLUMNS = (60, 200, 340, 480)


def _grid(page: pymupdf.Page, top: float, rows: int, columns: tuple[float, ...] = COLUMNS,
          first: int = 0) -> None:
    for row in range(rows):
        for column in range(len(columns) - 1):
            cell = pymupdf.Rect(columns[column], top + row * 20, columns[column + 1],
                                top + (row + 1) * 20)
            page.draw_rect(cell, width=0.8)
            page.insert_text((cell.x0 + 4, cell.y1 - 6), f"P-{first + row} c{column}",
                             fontsize=9)


def _report(path: Path, *, between: str | None = None,
            columns: tuple[float, ...] = COLUMNS) -> Path:
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((60, 40), "Pump inspection report", fontsize=9)  # running header
    first.insert_text((60, 100), "4. Results", fontsize=14)
    _grid(first, 600, 8)
    first.insert_text((290, 820), "Page 1", fontsize=9)  # page number
    second = document.new_page()
    second.insert_text((60, 40), "Pump inspection report", fontsize=9)
    top = 80
    if between:
        second.insert_text((60, 80), between, fontsize=12)
        top = 110
    _grid(second, top, 5, columns, first=8)
    second.insert_text((290, 820), "Page 2", fontsize=9)
    document.save(path)
    return path


def _tables(extracted: ExtractedDocument) -> list[GraphNode]:
    return sorted((n for n in extracted.nodes if n.type is NodeType.TABLE),
                  key=lambda n: n.locator.page or 0)


def _parent(extracted: ExtractedDocument, node: GraphNode) -> str:
    return next(e.source_id for e in extracted.edges
                if e.type is EdgeType.CONTAINS and e.target_id == node.id)


def test_a_table_broken_by_the_page_end_is_held_by_its_first_piece(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(_report(tmp_path / "r.pdf"))

    first, second = _tables(extracted)
    assert _parent(extracted, second) == first.id
    edge = next(e for e in extracted.edges if e.target_id == second.id)
    assert edge.metadata["basis"] == "continued_table"


def test_a_new_heading_above_the_second_table_starts_another_table(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(
        _report(tmp_path / "r.pdf", between="Table 5. Seal replacements"))

    first, second = _tables(extracted)
    assert _parent(extracted, second) == extracted.document.id


def test_tables_on_different_grids_are_two_tables(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(
        _report(tmp_path / "r.pdf", columns=(60, 150, 340, 480)))

    first, second = _tables(extracted)
    assert _parent(extracted, second) == extracted.document.id


def _png(width: int, height: int, colour: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, "PNG")
    return buffer.getvalue()


class _Store:
    def __init__(self) -> None:
        self.kept: dict[str, bytes] = {}

    def put(self, content: bytes, suffix: str = "") -> str:
        key = f"{len(self.kept) + 1:064x}"
        self.kept[key] = content
        return key

    def path_for(self, content_hash: str, suffix: str = "") -> Path:
        return Path(content_hash)

    def read(self, content_hash: str, suffix: str = "") -> bytes:
        return self.kept[content_hash]


def test_the_first_piece_is_cropped_with_the_second_under_it() -> None:
    store = _Store()
    where = SourceLocator(file_name="r.pdf", label="r")
    first = GraphNode(id="first", document_id="d", type=NodeType.TABLE, label="first",
                      locator=where, retrievable=False,
                      metadata={CROP_ASSET_KEY: store.put(_png(400, 160, "red"))})
    second = GraphNode(id="second", document_id="d", type=NodeType.TABLE, label="second",
                       locator=where, retrievable=False,
                       metadata={CROP_ASSET_KEY: store.put(_png(400, 100, "blue")),
                                 "crop_bbox": [0, 0, 400, 100]})
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r",
                     locator=where, retrievable=False)
    extracted = ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r.pdf", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r.pdf")),
        nodes=(root, first, second),
        edges=(GraphEdge(id="c1", document_id="d", source_id="d", target_id="first",
                         type=EdgeType.CONTAINS),
               GraphEdge(id="c2", document_id="d", source_id="first", target_id="second",
                         type=EdgeType.CONTAINS, metadata={"basis": "continued_table"})),
        evidence=(),
    )

    joined = join_continued(extracted, join=join_vertically, asset_store=store)

    held = {n.id: n for n in joined.nodes}
    with Image.open(io.BytesIO(store.read(str(held["first"].metadata[CROP_ASSET_KEY])))) as im:
        assert im.size == (400, 260)
        assert im.convert("RGB").getpixel((5, 200)) == (0, 0, 255)
    # Shown once, as part of the whole: the second piece keeps where it is drawn, not a
    # picture of its own an answer would be sent beside the whole.
    assert CROP_ASSET_KEY not in held["second"].metadata
    assert held["second"].metadata["crop_bbox"] == [0, 0, 400, 100]

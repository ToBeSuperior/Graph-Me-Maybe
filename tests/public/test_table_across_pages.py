"""A table the layout breaks over a page is shown whole: its pieces joined, top to bottom.

A flowing document's table is one node, drawn on every page it runs over. It was cropped once,
on the page showing most of it, so the rows on the other page were in no picture -- not in
the caption written from it, nor in what an answer was shown.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from graphme.adapters.rendering.visual_binding import join_vertically
from graphme.application.page_regions import PixelBox
from graphme.application.visual_crops import CROP_ASSET_KEY, stitch_across_pages
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    SourceLocator,
)


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


class _Placer:
    def __init__(self, boxes: dict[str, PixelBox]) -> None:
        self.boxes = boxes

    def box(self, node: GraphNode) -> PixelBox | None:
        return self.boxes.get(node.id)


def _document(store: _Store) -> ExtractedDocument:
    where = SourceLocator(file_name="r.docx", label="r")
    home_crop = store.put(_png(400, 300, "red"))
    table = GraphNode(id="table", document_id="d", type=NodeType.TABLE, label="table",
                      locator=where, retrievable=False,
                      metadata={CROP_ASSET_KEY: home_crop, "crop_page_index": 3,
                                "crop_bbox": [50, 900, 450, 1200]})
    photo = GraphNode(id="photo", document_id="d", type=NodeType.IMAGE, label="photo",
                      locator=where, retrievable=False,
                      metadata={CROP_ASSET_KEY: store.put(_png(10, 10, "green")),
                                "crop_page_index": 3})
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r",
                     locator=where, retrievable=False)
    return ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r.docx", content_hash="b" * 64,
                                format=DocumentFormat.DOCX, size_bytes=1,
                                source_path=Path("r.docx")),
        nodes=(root, table, photo),
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d", source_id="d",
                              target_id=n.id, type=EdgeType.CONTAINS) for n in (table, photo)),
        evidence=(),
    )


def test_a_table_drawn_on_two_pages_is_cropped_from_both(tmp_path: Path) -> None:
    store = _Store()
    extracted = _document(store)
    placers = {
        3: _Placer({"table": PixelBox(50, 900, 450, 1200), "photo": PixelBox(0, 0, 10, 10)}),
        4: _Placer({"table": PixelBox(50, 80, 410, 180)}),  # the rows run over the break
    }
    cut: list[tuple[int, tuple[int, int, int, int]]] = []

    def cut_page(index: int, box: tuple[int, int, int, int]) -> bytes:
        cut.append((index, box))
        return _png(box[2] - box[0], box[3] - box[1], "blue")

    joined = stitch_across_pages(extracted, placers=placers, cut=cut_page, join=join_vertically,
                                 asset_store=store)

    table = next(n for n in joined.nodes if n.id == "table")
    with Image.open(io.BytesIO(store.read(str(table.metadata[CROP_ASSET_KEY])))) as image:
        rgb = image.convert("RGB")
        # The page showing most of it first, as it was cropped; the rest under it.
        assert rgb.size == (400, 300 + 100)
        assert rgb.getpixel((10, 10)) == (255, 0, 0)
        assert rgb.getpixel((10, 350)) == (0, 0, 255)
    assert cut == [(4, (50, 80, 410, 180))]
    # Where it is shown on the page stays the page it was bound on.
    assert table.metadata["crop_page_index"] == 3
    # A picture on one page is left as it was.
    photo = next(n for n in joined.nodes if n.id == "photo")
    assert photo == next(n for n in extracted.nodes if n.id == "photo")

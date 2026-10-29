"""A scanned page is read by the page-reading model; a page with words of its own is not.

A scan is a picture of a page: the file holds no words for it, so nothing on it reached
search. Which pages are scans is judged from the file alone -- a page whose only content is
one picture, or a page its pictures cover nearly whole with no words of the file's over them
-- and only those pages are sent to be read. What comes back is the model's reading, not the
file's text, and says so; the scan stays as the picture the answer is shown.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.application.page_regions import PixelBox
from graphme.application.visual_captions import caption_visuals
from graphme.application.visual_crops import CROP_ASSET_KEY, bind_page_visuals
from graphme.domain.enums import DocumentFormat, EdgeType, EvidenceKind, NodeType
from graphme.domain.models import (
    DocumentRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    ScannedBlock,
    ScannedPage,
    SourceLocator,
    VisualCaption,
    VisualCaptionResult,
)

A4 = pymupdf.paper_rect("a4")


def _picture(width: int = 600, height: int = 800) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 235)).save(buffer, "JPEG")
    return buffer.getvalue()


def _pdf(path: Path, *pages: str) -> Path:
    """Pages by kind: a full scan, a scan with a page number stamped under it, a scan kept
    as two strips, a page of text with a small figure, a small logo alone, a scan laid inside
    margins with a filing header stamped across its top, and a page of text printed over a
    picture filling it."""
    document = pymupdf.open()
    w, h = A4.width, A4.height
    for kind in pages:
        page = document.new_page(width=w, height=h)

        def place(x0: float, y0: float, x1: float, y1: float, picture: bytes = _picture(),
                  page: pymupdf.Page = page) -> None:
            page.insert_image(pymupdf.Rect(x0, y0, x1, y1), stream=picture,
                              keep_proportion=False)

        if kind == "scan":
            place(0, 0, w, h)
        elif kind == "stamped":
            place(0, 0, w, h * 0.9)
            page.insert_text((w / 2, h * 0.96), "- 2 -", fontsize=10)
        elif kind == "strips":
            place(0, 0, w, h / 2)
            place(0, h / 2, w, h)
        elif kind == "text":
            page.insert_text((60, 80), "4. Results", fontsize=14)
            page.insert_text((60, 110), "The pump ran 300 hours without a fault.", fontsize=11)
            place(150, 300, 450, 500)
        elif kind == "logo":
            place(250, 350, 345, 445, _picture(90, 90))
        elif kind == "filed":
            place(54, 30, w - 54, h - 72)
            page.insert_text((60, 40), "Case 4:20-cv-00001 Document 12-3 Filed 04/13/20 Page 2",
                             fontsize=9)
        elif kind == "printed-over":
            place(0, 0, w, h)
            for line in range(12):
                page.insert_text((60, 100 + 40 * line), f"Line {line} of the brochure text.",
                                 fontsize=12)
    document.save(path)
    return path


READING = ScannedPage(has_text=True, blocks=(
    ScannedBlock(text="3.1 Inspection interval", box=(0.08, 0.06, 0.6, 0.09)),
    ScannedBlock(text="Every pump is inspected each 2,000 hours.", box=(0.08, 0.1, 0.9, 0.14)),
))


class _Reader:
    def __init__(self, answer: ScannedPage = READING) -> None:
        self.answer = answer
        self.pages: list[bytes] = []

    def __call__(self, image: bytes) -> ScannedPage:
        self.pages.append(image)
        return self.answer


class _Store:
    def __init__(self) -> None:
        self.kept: dict[str, bytes] = {}

    def put(self, payload: bytes, *, suffix: str = "") -> str:
        key = f"{len(self.kept):064x}"
        self.kept[key] = payload
        return key


def _read(path: Path, reader: _Reader, store: _Store | None = None) -> ExtractedDocument:
    return PDFExtractor(asset_store=store, read_scan=reader).extract(path)


def _texts(extracted: ExtractedDocument) -> list[GraphNode]:
    return [n for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK]


def test_a_scanned_page_is_read_and_its_words_are_the_models(tmp_path: Path) -> None:
    store = _Store()
    extracted = _read(_pdf(tmp_path / "s.pdf", "scan"), reader := _Reader(), store)

    assert len(reader.pages) == 1
    read = _texts(extracted)
    assert [n.text for n in read] == [b.text for b in READING.blocks]
    for node in read:
        kinds = {e.kind: e for e in extracted.evidence if e.node_id == node.id}
        assert kinds[EvidenceKind.GENERATED_EXTRACTION].generated
        # What the words were read off: the picture the file holds.
        assert not kinds[EvidenceKind.SOURCE_MEDIA].generated
    scan = next(n for n in extracted.nodes if n.type is NodeType.IMAGE)
    # The scan is what the page is drawn on, and the picture an answer is shown.
    assert scan.metadata["background"] is True
    assert store.kept[str(scan.metadata[CROP_ASSET_KEY])] == reader.pages[0]
    assert {n.metadata["scan_of"] for n in read} == {scan.id}
    # Placed where the model saw them, in page coordinates.
    first = read[0].locator.bbox
    assert first is not None and abs(first[1] - 0.06 * A4.height) < 1
    assert [w.code for w in extracted.warnings] == ["pdf_scanned_page"]


def test_only_pages_that_are_pictures_are_sent_to_be_read(tmp_path: Path) -> None:
    reader = _Reader()
    extracted = _read(_pdf(tmp_path / "m.pdf", "text", "stamped", "strips", "logo"), reader)

    # The stamped scan, the strips and the logo alone; never the page of text.
    assert len(reader.pages) == 3
    assert "The pump ran 300 hours without a fault." in [n.text for n in _texts(extracted)]


def test_a_picture_with_nothing_to_read_stays_a_picture(tmp_path: Path) -> None:
    extracted = _read(_pdf(tmp_path / "p.pdf", "scan"),
                      _Reader(ScannedPage(has_text=False, blocks=())))

    assert _texts(extracted) == []
    scan = next(n for n in extracted.nodes if n.type is NodeType.IMAGE)
    assert "background" not in scan.metadata
    assert "pdf_scanned_page" not in [w.code for w in extracted.warnings]


def test_a_page_read_as_nothing_but_unreadable_marks_is_kept_as_a_picture(
    tmp_path: Path,
) -> None:
    unreadable = ScannedPage(has_text=True, blocks=(
        ScannedBlock(text="[?] [?]", box=(0.1, 0.1, 0.9, 0.2)),
        ScannedBlock(text="[?].", box=(0.1, 0.3, 0.9, 0.4)),
    ))
    extracted = _read(_pdf(tmp_path / "u.pdf", "scan"), _Reader(unreadable))

    assert _texts(extracted) == []
    assert "background" not in next(
        n for n in extracted.nodes if n.type is NodeType.IMAGE).metadata


def test_without_a_reader_a_scan_only_pdf_is_still_said_to_be_unread(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(_pdf(tmp_path / "s.pdf", "scan"))

    assert _texts(extracted) == []
    assert [w.code for w in extracted.warnings] == ["pdf_scan_only"]


@dataclass
class _Page:
    index: int = 1
    width: int = 1000
    height: int = 1400
    png: bytes = b"\x89PNG\r\n\x1a\n"
    label: str | None = None


def test_a_scan_smaller_than_the_page_does_not_take_the_words_read_off_it() -> None:
    # A scan with margins around it: neither the share nor the band that makes a backdrop on
    # its own, and a backdrop because the reading said so.
    boxes = {"scan": PixelBox(50, 30, 940, 1340), "line": PixelBox(80, 80, 900, 130)}
    where = SourceLocator(file_name="r", label="r", page=1)
    scan = GraphNode(id="scan", document_id="d", type=NodeType.IMAGE, label="scan",
                     locator=where, retrievable=False,
                     metadata={"background": True, CROP_ASSET_KEY: "a" * 64})
    line = GraphNode(id="line", document_id="d", type=NodeType.TEXT_CHUNK, label="line",
                     text="line", locator=where, retrievable=False,
                     metadata={"scan_of": "scan"})
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r",
                     locator=where, retrievable=False)
    extracted = ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r")),
        nodes=(root, scan, line),
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d", source_id="d",
                              target_id=n.id, type=EdgeType.CONTAINS) for n in (scan, line)),
        evidence=(),
    )

    class _Placer:
        def box(self, node: GraphNode) -> PixelBox | None:
            return boxes.get(node.id)

    bound = bind_page_visuals(extracted, page=_Page(), members=(scan, line), placer=_Placer(),
                              asset_store=_Store(), crop=lambda box: b"png")

    owners = {e.target_id: e.source_id for e in bound.edges if e.type is EdgeType.CONTAINS}
    assert owners["line"] == "d"
    kept = next(n for n in bound.nodes if n.id == "scan")
    assert kept.metadata[CROP_ASSET_KEY] == "a" * 64


class _Captioner:
    def __init__(self) -> None:
        self.asked: list[tuple[str, ...]] = []

    def caption_visuals(self, *, request, image, unit_images=None):  # type: ignore[no-untyped-def]
        self.asked.append(tuple(unit.id for unit in request.units))
        return VisualCaptionResult(captions=tuple(
            VisualCaption(subject_id=u.id, caption="a pump", confidence=0.9)
            for u in request.units))


class _Crops:
    def read(self, asset_hash: str, suffix: str = "") -> bytes:
        return _picture(10, 10)


def test_a_scan_read_for_its_words_is_not_described_again() -> None:
    # Its words are already the page's; a caption would say them a second time.
    where = SourceLocator(file_name="r", label="r", page=1)
    nodes = tuple(
        GraphNode(id=node_id, document_id="d", type=NodeType.IMAGE, label=node_id,
                  locator=where, retrievable=False, metadata=metadata)
        for node_id, metadata in (
            ("scan", {CROP_ASSET_KEY: "a" * 64, "background": True}),
            ("photo", {CROP_ASSET_KEY: "b" * 64}),
        ))
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r",
                     locator=where, retrievable=False)
    extracted = ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r", content_hash="b" * 64,
                                format=DocumentFormat.PDF, size_bytes=1,
                                source_path=Path("r")),
        nodes=(root, *nodes),
        edges=tuple(GraphEdge(id=f"c-{n.id}", document_id="d", source_id="d",
                              target_id=n.id, type=EdgeType.CONTAINS) for n in nodes),
        evidence=(),
    )

    captioner = _Captioner()
    caption_visuals(extracted, pages=(_Page(),), vision=captioner,  # type: ignore[arg-type]
                    asset_store=_Crops())  # type: ignore[arg-type]

    assert captioner.asked == [("photo",)]


def test_words_the_file_prints_beside_the_scan_are_not_read_twice(tmp_path: Path) -> None:
    # The model sees the whole page, page number and all; only what is on the scan is new.
    reading = ScannedPage(has_text=True, blocks=(
        ScannedBlock(text="Appendix B. Calibration Record", box=(0.1, 0.05, 0.6, 0.08)),
        ScannedBlock(text="- 2 -", box=(0.47, 0.95, 0.53, 0.97)),
    ))
    extracted = _read(_pdf(tmp_path / "s.pdf", "stamped"), _Reader(reading))

    assert sorted(n.text or "" for n in _texts(extracted)) == [
        "- 2 -", "Appendix B. Calibration Record"]


def test_a_scan_inside_margins_with_a_filing_header_over_it_is_read(tmp_path: Path) -> None:
    # Laid inside half-inch margins it covers well under 85% of the page, and the court's
    # header is stamped across its top: a line of the file's words over the scan.
    reading = ScannedPage(has_text=True, blocks=(
        ScannedBlock(text="Case 4:20-cv-00001 Document 12-3 Filed 04/13/20 Page 2",
                     box=(0.09, 0.035, 0.6, 0.05)),
        ScannedBlock(text="EXECUTIVE ORDER", box=(0.35, 0.1, 0.65, 0.13)),
    ))
    extracted = _read(_pdf(tmp_path / "f.pdf", "filed"), reader := _Reader(reading))

    assert len(reader.pages) == 1
    # The header is the file's own words, already a unit; it is not read a second time.
    assert sorted(n.text or "" for n in _texts(extracted)) == [
        "Case 4:20-cv-00001 Document 12-3 Filed 04/13/20 Page 2", "EXECUTIVE ORDER"]


def test_a_page_of_text_printed_over_a_picture_is_not_a_scan(tmp_path: Path) -> None:
    _read(_pdf(tmp_path / "b.pdf", "printed-over"), reader := _Reader())

    assert reader.pages == []


def test_a_page_whose_reading_is_only_the_files_own_words_stays_as_it_was(
    tmp_path: Path,
) -> None:
    # Photographs under a filing header: the model reads back the header and nothing else.
    reading = ScannedPage(has_text=True, blocks=(
        ScannedBlock(text="Case 4:20-cv-00001 Document 12-3 Filed 04/13/20 Page 2",
                     box=(0.09, 0.035, 0.6, 0.05)),
    ))
    extracted = _read(_pdf(tmp_path / "f.pdf", "filed"), _Reader(reading))

    assert [n.text for n in _texts(extracted)] == [
        "Case 4:20-cv-00001 Document 12-3 Filed 04/13/20 Page 2"]
    assert "background" not in next(
        n for n in extracted.nodes if n.type is NodeType.IMAGE).metadata
    assert "pdf_scanned_page" not in [w.code for w in extracted.warnings]


def test_a_picture_filling_a_slide_with_nothing_over_it_is_the_slide_not_its_backdrop() -> None:
    # A scan pasted to fill a slide: nothing is printed on it, so it is what the slide says --
    # cropped and captioned -- not the ground something else is drawn on.
    where = SourceLocator(file_name="r", label="r", slide=1)
    scan = GraphNode(id="scan", document_id="d", type=NodeType.IMAGE, label="scan",
                     locator=where, retrievable=False)
    root = GraphNode(id="d", document_id="d", type=NodeType.DOCUMENT, label="r",
                     locator=where, retrievable=False)
    extracted = ExtractedDocument(
        document=DocumentRecord(id="d", file_name="r", content_hash="b" * 64,
                                format=DocumentFormat.PPTX, size_bytes=1,
                                source_path=Path("r")),
        nodes=(root, scan),
        edges=(GraphEdge(id="c-scan", document_id="d", source_id="d", target_id="scan",
                         type=EdgeType.CONTAINS),),
        evidence=(),
    )

    class _Placer:
        def box(self, node: GraphNode) -> PixelBox | None:
            return PixelBox(0, 0, 1000, 1400)

    bound = bind_page_visuals(extracted, page=_Page(), members=(scan,), placer=_Placer(),
                              asset_store=_Store(), crop=lambda box: b"png")

    kept = next(n for n in bound.nodes if n.id == "scan")
    assert "background" not in kept.metadata
    assert CROP_ASSET_KEY in kept.metadata

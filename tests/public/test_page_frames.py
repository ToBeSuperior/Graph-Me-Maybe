"""A page drawn inside a frame with a band down its side is not a table.

The frame, the band's edge and a rule across the page meet in a grid, and the table finder
returns it: four cells, the page's text in one of them and a few words in another. Half its
cells are filled, so "mostly empty" did not give it away, and every page of a report became
one table holding the page. What gives it away is that one cell holds nearly all its text.
Invented pages."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.domain.enums import NodeType

GREEN = (0.3, 0.5, 0.3)
BODY = "Pump inspections were moved to a fixed interval of two thousand running hours. " * 30


def _framed_report(path: Path) -> Path:
    document = pymupdf.open()
    for _ in range(2):
        page = document.new_page(width=612, height=792)
        page.draw_rect(pymupdf.Rect(23, 23, 589, 769), color=GREEN, width=1)
        page.draw_line((66, 23), (66, 769), color=GREEN, width=1)  # the band down the side
        page.draw_line((23, 480), (589, 480), color=GREEN, width=1)
        page.insert_textbox(pymupdf.Rect(80, 40, 580, 470), BODY, fontsize=10)
        page.insert_textbox(pymupdf.Rect(28, 500, 62, 760), "Maintenance review",
                            fontsize=8, rotate=90)
    document.save(path)
    return path


def _glossary(path: Path) -> Path:
    """A real two-column table whose second column is written in sentences."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    terms = ("Interval", "Overhaul", "Seal kit", "Run-in")
    rows = [100 + 120 * n for n in range(len(terms) + 1)]
    for y in rows:
        page.draw_line((40, y), (572, y), color=(0, 0, 0), width=1)
    for x in (40, 160, 572):
        page.draw_line((x, rows[0]), (x, rows[-1]), color=(0, 0, 0), width=1)
    for n, term in enumerate(terms):
        page.insert_textbox(pymupdf.Rect(46, rows[n] + 6, 154, rows[n + 1] - 6), term,
                            fontsize=10)
        page.insert_textbox(pymupdf.Rect(166, rows[n] + 6, 566, rows[n + 1] - 6),
                            f"{term} is the work done on a pump between two inspections, "
                            "logged against its running hours and signed off by the "
                            "shift lead before it goes back into service, with the parts "
                            "changed and the hours it ran listed on the card.", fontsize=9)
    document.save(path)
    return path


def test_a_page_framed_with_a_band_down_its_side_is_read_as_text(tmp_path: Path) -> None:
    extracted = PDFExtractor(asset_store=None).extract(_framed_report(tmp_path / "r.pdf"))

    assert not [n for n in extracted.nodes if n.type is NodeType.TABLE]
    words = " ".join(n.text or "" for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK)
    assert "two thousand running hours" in words and "Maintenance review" in words


def test_a_table_written_in_sentences_stays_a_table(tmp_path: Path) -> None:
    extracted = PDFExtractor(asset_store=None).extract(_glossary(tmp_path / "g.pdf"))

    assert len([n for n in extracted.nodes if n.type is NodeType.TABLE]) == 1


def _one_row_of_notes(path: Path) -> Path:
    """A table's single row: a topic beside a paragraph about it, filling a third of the page."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    for y in (200, 480):
        page.draw_line((40, y), (572, y), color=(0, 0, 0), width=1)
    for x in (40, 150, 572):
        page.draw_line((x, 200), (x, 480), color=(0, 0, 0), width=1)
    page.insert_textbox(pymupdf.Rect(46, 206, 144, 474), "Seal changes", fontsize=10)
    page.insert_textbox(pymupdf.Rect(156, 206, 566, 474), BODY[:900], fontsize=10)
    document.save(path)
    return path


def test_a_table_of_one_row_setting_a_paragraph_beside_its_topic_stays_a_table(
    tmp_path: Path,
) -> None:
    extracted = PDFExtractor(asset_store=None).extract(_one_row_of_notes(tmp_path / "n.pdf"))

    assert len([n for n in extracted.nodes if n.type is NodeType.TABLE]) == 1


def _three_column_panel(path: Path) -> Path:
    """A page laid out in ruled columns: a heading beside two columns of running text. One
    row, every column filled -- the rule cannot tell it from a table."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.draw_rect(pymupdf.Rect(30, 40, 582, 760), color=(0.8, 0.1, 0.1), width=1)
    for x in (200, 390):
        page.draw_line((x, 40), (x, 760), color=(0.8, 0.1, 0.1), width=1)
    page.insert_textbox(pymupdf.Rect(36, 50, 194, 750), "Service review of the year", fontsize=14)
    for x0, x1 in ((206, 384), (396, 576)):
        page.insert_textbox(pymupdf.Rect(x0, 50, x1, 750), BODY[:1500], fontsize=9)
    document.save(path)
    return path


def test_a_table_over_half_the_page_is_read_as_text_when_the_model_sees_a_layout(
    tmp_path: Path,
) -> None:
    shown: list[bytes] = []

    def layout(image: bytes) -> bool:
        shown.append(image)
        return False

    extracted = PDFExtractor(asset_store=None, check_table=layout).extract(
        _three_column_panel(tmp_path / "p.pdf"))

    assert len(shown) == 1 and shown[0].startswith(b"\x89PNG")
    assert not [n for n in extracted.nodes if n.type is NodeType.TABLE]
    words = " ".join(n.text or "" for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK)
    assert "Service review of the year" in words and "two thousand running hours" in words


def test_a_table_the_model_confirms_or_cannot_answer_for_stays_a_table(tmp_path: Path) -> None:
    def broken(image: bytes) -> bool:
        raise RuntimeError("the model endpoint did not answer")

    for check in (lambda image: True, broken):
        extracted = PDFExtractor(asset_store=None, check_table=check).extract(
            _three_column_panel(tmp_path / "p.pdf"))
        assert len([n for n in extracted.nodes if n.type is NodeType.TABLE]) == 1


def test_a_table_under_half_the_page_is_not_asked_about(tmp_path: Path) -> None:
    asked: list[bytes] = []
    PDFExtractor(asset_store=None, check_table=lambda image: asked.append(image) or True).extract(
        _one_row_of_notes(tmp_path / "n.pdf"))

    assert asked == []


def test_the_model_is_asked_whether_the_outlined_region_is_a_table() -> None:
    import json

    import httpx

    from graphme.adapters.vision.chat_completions import ChatCompletionsVLMAdapter

    sent: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        answer = {"finish_reason": "stop", "message": {"content": '{"kind": "layout"}'}}
        return httpx.Response(200, json={"choices": [answer]})

    adapter = ChatCompletionsVLMAdapter(
        base_url="https://vision.test/v1", model="m",
        client=httpx.Client(transport=httpx.MockTransport(handle)), wait=lambda _: None)
    page = pymupdf.open()
    page.new_page(width=100, height=100)

    assert adapter.is_a_table(page[0].get_pixmap().tobytes("png")) is False
    body = json.dumps(sent[0])
    assert "image_url" in body and "outlined" in body

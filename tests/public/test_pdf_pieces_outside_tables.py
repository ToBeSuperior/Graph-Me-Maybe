"""A paragraph's printed pieces leave out the table standing beside it. Invented page.

pymupdf groups a band into one block, so a paragraph down the left of a table arrives holding
the table's cells. Extraction drops those lines (`_outside_tables`); the pieces text grouping
joins again must drop them too, or the paragraph takes the table's words back.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from graphme.adapters.extractors.pdf import PDFExtractor, printed_pieces
from graphme.domain.enums import NodeType


def _paragraph_beside_a_table(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=400, height=300)
    for y in (52, 64, 76):
        page.draw_line((200, y), (380, y))
    for x in (200, 290, 380):
        page.draw_line((x, 52), (x, 76))
    page.insert_text((20, 50), "The orchard report opens here and runs wide across the whole",
                     fontsize=10)
    page.insert_text((20, 62), "page beside a small", fontsize=10)
    page.insert_text((205, 62), "Alpha", fontsize=10)
    page.insert_text((295, 62), "Beta", fontsize=10)
    page.insert_text((20, 74), "table of values.", fontsize=10)
    page.insert_text((205, 74), "Gamma", fontsize=10)
    page.insert_text((295, 74), "Delta", fontsize=10)
    document.save(path)


def test_printed_pieces_leave_out_the_table_beside_the_paragraph(tmp_path: Path) -> None:
    pdf = tmp_path / "orchard.pdf"
    _paragraph_beside_a_table(pdf)
    extracted = PDFExtractor().extract(pdf)
    assert any(node.type is NodeType.TABLE for node in extracted.nodes)

    pieces, _ = printed_pieces(pdf, extracted.nodes)

    printed = [piece for lines in pieces.values() for piece in lines]
    assert any("orchard" in piece for piece in printed)
    assert not any(cell in piece for piece in printed
                   for cell in ("Alpha", "Beta", "Gamma", "Delta"))


def test_a_large_table_is_drawn_for_its_check_in_the_calling_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pymupdf document is not to be shared across threads: the outlined page is drawn
    where the extractor runs, and only the model's check runs beside it."""
    import threading

    from graphme.adapters.extractors import pdf as pdf_module

    path = tmp_path / "ledger.pdf"
    document = pymupdf.open()
    for _ in range(3):
        page = document.new_page(width=400, height=300)
        for y in (20, 150, 280):
            page.draw_line((20, y), (380, y))
        for x in (20, 200, 380):
            page.draw_line((x, 20), (x, 280))
        for x, y, word in ((30, 80, "North"), (210, 80, "South"),
                           (30, 210, "East"), (210, 210, "West")):
            page.insert_text((x, y), word, fontsize=10)
    document.save(path)
    drawn_in: set[str] = set()

    def outlined(page: object, bbox: object) -> bytes:
        drawn_in.add(threading.current_thread().name)
        return b"outlined"

    monkeypatch.setattr(pdf_module, "_outlined", outlined)
    checked: list[bytes] = []

    def check(picture: bytes) -> bool:
        checked.append(picture)
        return True

    PDFExtractor(check_table=check, workers=3).extract(path)

    assert len(checked) == 3
    assert drawn_in == {threading.current_thread().name}

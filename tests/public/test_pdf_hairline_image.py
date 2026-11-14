"""A picture narrower than a pixel is not a picture of the page. A browser's print of a drawn rule
holds one (a strip a fraction of a point wide); cut from the page, it came to no pixel at all and
the whole document failed to read. Invented page."""
from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.domain.enums import NodeType


def _page_with_a_hairline(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    strip = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 5))  # type: ignore[no-untyped-call]
    strip.clear_with(0)  # type: ignore[no-untyped-call]
    page.insert_image(pymupdf.Rect(100, 100, 100.00006, 105), pixmap=strip)  # type: ignore[no-untyped-call]
    page.insert_text((72, 72), "Invented notice")  # type: ignore[no-untyped-call]
    document.save(path)
    return path


@pytest.mark.parametrize("held", [True, False], ids=["picture held", "picture inline"])
def test_a_hairline_picture_is_no_unit_and_the_page_still_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: bool
) -> None:
    if not held:  # a picture the file does not hold apart is cut from the page instead
        monkeypatch.setattr(pymupdf.Document, "extract_image", lambda *a: {})
    extracted = PDFExtractor().extract(_page_with_a_hairline(tmp_path / "n.pdf"))

    assert not [n for n in extracted.nodes if n.type is NodeType.IMAGE]
    assert any("Invented notice" in (n.text or "") for n in extracted.nodes)

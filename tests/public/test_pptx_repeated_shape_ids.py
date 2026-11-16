"""Shape numbers repeat: a pasted shape keeps its number, and every slide counts its own. Each
shape is still a node of its own. Invented deck."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from graphme.adapters.extractors.powerpoint import PowerPointExtractor
from graphme.domain.enums import NodeType


def test_two_shapes_sharing_a_number_are_two_nodes(tmp_path: Path) -> None:
    deck = Presentation()
    for words in (("Inlet", "Outlet"), ("Pump", "Valve")):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        first = slide.shapes.add_textbox(0, 0, Inches(2), Inches(1))
        second = slide.shapes.add_textbox(0, Inches(2), Inches(2), Inches(1))
        first.text_frame.text, second.text_frame.text = words
        second._element.nvSpPr.cNvPr.set("id", str(first.shape_id))  # pasted: same number
    deck.save(tmp_path / "deck.pptx")

    extracted = PowerPointExtractor().extract(tmp_path / "deck.pptx")

    texts = [n for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK]
    assert sorted(n.text or "" for n in texts) == ["Inlet", "Outlet", "Pump", "Valve"]
    assert len({n.id for n in texts}) == 4

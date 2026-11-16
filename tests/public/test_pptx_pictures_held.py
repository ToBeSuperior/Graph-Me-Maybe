"""A picture placed in a placeholder is a picture; a linked one is not a reason to fail.
Invented deck."""
from __future__ import annotations

from base64 import b64decode
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from graphme.adapters.extractors.powerpoint import PowerPointExtractor
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.domain.enums import NodeType

PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _images(tmp_path: Path, deck: Path) -> list[str]:
    extracted = PowerPointExtractor(asset_store=LocalAssetStore(tmp_path / "a")).extract(deck)
    return [n.label for n in extracted.nodes if n.type is NodeType.IMAGE]


def test_a_picture_in_a_picture_placeholder_is_an_image(tmp_path: Path) -> None:
    (tmp_path / "pear.png").write_bytes(PNG)
    deck = Presentation()
    layout = next(layout for layout in deck.slide_layouts
                  if any(p.placeholder_format.type == 18 for p in layout.placeholders))
    slide = deck.slides.add_slide(layout)
    holder = next(p for p in slide.placeholders if p.placeholder_format.type == 18)
    holder.insert_picture(str(tmp_path / "pear.png"))
    deck.save(tmp_path / "deck.pptx")

    assert len(_images(tmp_path, tmp_path / "deck.pptx")) == 1


def test_a_linked_picture_does_not_fail_the_deck(tmp_path: Path) -> None:
    (tmp_path / "plum.png").write_bytes(PNG)
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    picture = slide.shapes.add_picture(str(tmp_path / "plum.png"), 0, 0, Inches(1), Inches(1))
    blip = picture._element.blipFill.blip
    blip.set(f"{{{R}}}link", blip.get(f"{{{R}}}embed"))
    del blip.attrib[f"{{{R}}}embed"]
    slide.shapes.add_textbox(0, Inches(2), Inches(3), Inches(1)).text_frame.text = "Plum notes"
    deck.save(tmp_path / "deck.pptx")

    extracted = PowerPointExtractor(asset_store=LocalAssetStore(tmp_path / "a")).extract(
        tmp_path / "deck.pptx")
    assert any(n.text == "Plum notes" for n in extracted.nodes)

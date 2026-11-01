"""A PDF line set in with spaces is read as set in.

A report nests its items by starting the line with spaces. The spaces are glyphs of their
own, so the line's left edge is where the first space stands -- the same as the heading's --
and the text was trimmed when it was read: the item came out level with its heading. The
spaces are now counted, as a DOCX paragraph's are, and the outline reads them with the edge.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from graphme.adapters.extractors.pdf import PDFExtractor
from graphme.application.page_hierarchy_scope import _indent_levels
from graphme.domain.enums import NodeType


def _report(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 80), "3. Inspection", fontsize=12)
    page.insert_text((60, 120), "    a) bearing temperature below 85 C", fontsize=11)
    page.insert_text((60, 160), "4. Records", fontsize=12)
    document.save(path)
    return path


def test_a_line_begun_with_spaces_is_read_one_step_in(tmp_path: Path) -> None:
    extracted = PDFExtractor().extract(_report(tmp_path / "r.pdf"))

    units = {n.text: n for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK}
    assert set(units) == {"3. Inspection", "a) bearing temperature below 85 C", "4. Records"}
    spaces = {text: node.metadata.get("leading_spaces") for text, node in units.items()}
    assert spaces == {"3. Inspection": 0, "a) bearing temperature below 85 C": 4,
                      "4. Records": 0}
    levels = _indent_levels([(node.id, 60, int(node.metadata["leading_spaces"]))
                             for node in units.values()])
    assert levels[units["a) bearing temperature below 85 C"].id] == 1
    assert levels[units["3. Inspection"].id] == levels[units["4. Records"].id] == 0

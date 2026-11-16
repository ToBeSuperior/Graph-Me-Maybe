"""What a Word paragraph holds one level down is still the paragraph's: a picture inside a
link, a tracked insertion or a content control, a text box anchored in a table cell, and a
paragraph whose numbering says "none". Invented memo."""
from __future__ import annotations

from base64 import b64decode
from pathlib import Path

import docx
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from graphme.adapters.extractors.docx import DocxExtractor
from graphme.domain.enums import NodeType

PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
WRAPPERS = {
    "hyperlink": f'<w:hyperlink {nsdecls("w", "r")} r:id="rIdX"/>',
    "ins": f'<w:ins {nsdecls("w")} w:id="1" w:author="A" w:date="2026-01-01T00:00:00Z"/>',
    "sdt": f'<w:sdt {nsdecls("w")}><w:sdtContent/></w:sdt>',
}


def test_a_picture_inside_a_link_an_insertion_or_a_content_control_is_an_image(
    tmp_path: Path,
) -> None:
    (tmp_path / "pear.png").write_bytes(PNG)
    memo = docx.Document()
    for name, xml in WRAPPERS.items():
        paragraph = memo.add_paragraph(f"Figure in a {name}: ")
        run = paragraph.add_run()
        run.add_picture(str(tmp_path / "pear.png"))
        wrapper = parse_xml(xml)
        holder = wrapper.find(qn("w:sdtContent")) if name == "sdt" else wrapper
        run._r.addprevious(wrapper)
        holder.append(run._r)
    memo.save(tmp_path / "memo.docx")

    extracted = DocxExtractor().extract(tmp_path / "memo.docx")

    images = [n for n in extracted.nodes if n.type is NodeType.IMAGE]
    assert sorted(n.metadata["body_index"] for n in images) == [1, 2, 3]


def test_a_text_box_anchored_in_a_table_cell_is_a_text_box(tmp_path: Path) -> None:
    memo = docx.Document()
    cell = memo.add_table(rows=1, cols=2).cell(0, 1)
    cell.paragraphs[0]._p.append(parse_xml(
        f'<w:r {nsdecls("w")} xmlns:v="urn:schemas-microsoft-com:vml">'
        "<w:pict><v:shape><v:textbox><w:txbxContent>"
        "<w:p><w:r><w:t>Callout: torque 12 Nm</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"))
    memo.save(tmp_path / "memo.docx")

    extracted = DocxExtractor().extract(tmp_path / "memo.docx")

    boxes = [n for n in extracted.nodes if n.metadata.get("story") == "textbox"]
    assert [(n.text, n.metadata["body_index"], n.metadata["box_index"]) for n in boxes] == [
        ("Callout: torque 12 Nm", 1, 1)]


def test_numbering_zero_means_no_number(tmp_path: Path) -> None:
    memo = docx.Document()
    paragraph = memo.add_paragraph("Plain line")
    paragraph._p.get_or_add_pPr().append(parse_xml(
        f'<w:numPr {nsdecls("w")}><w:ilvl w:val="0"/><w:numId w:val="0"/></w:numPr>'))
    memo.save(tmp_path / "memo.docx")

    extracted = DocxExtractor().extract(tmp_path / "memo.docx")

    assert [n.text for n in extracted.nodes if n.type is NodeType.TEXT_CHUNK] == ["Plain line"]

"""What the HWP renderer leaves out of its print is kept: the file's own paragraphs are read
beside the print, and those the print does not hold go on pages after it. Invented text."""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pymupdf

from graphme.adapters.rendering.hwp_print import (
    file_paragraphs,
    hwp_record_paragraphs,
    keep_undrawn,
    undrawn,
)

SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:t>Invented notice of the year</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>Before table</hp:t>
    <hp:tbl><hp:tr><hp:tc><hp:subList>
      <hp:p><hp:run><hp:t>첫 칸 &amp; 내용</hp:t></hp:run></hp:p>
    </hp:subList></hp:tc></hp:tr></hp:tbl>
  </hp:run></hp:p>
  <hp:p><hp:run><hp:t>Split<hp:tab/>by a tab</hp:t></hp:run></hp:p>
</hs:sec>"""


def _hwpx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", SECTION)
    return path


def test_an_hwpx_paragraph_is_its_own_words_and_a_cell_is_a_paragraph(tmp_path: Path) -> None:
    assert file_paragraphs(_hwpx(tmp_path / "n.hwpx")) == [
        "Invented notice of the year", "Before table", "첫 칸 & 내용", "Split\tby a tab"]


def _record(tag: int, payload: bytes) -> bytes:
    return struct.pack("<I", tag | (len(payload) << 20)) + payload


def test_hwp_records_give_each_paragraph_text_without_its_controls() -> None:
    def units(*values: int) -> bytes:
        return struct.pack(f"<{len(values)}H", *values)

    words = "Invented 칸".encode("utf-16-le")
    table = units(11, 0x6C74, 0x6274, 0, 0, 0, 0, 11)  # an extended control: eight units
    data = (_record(66, b"\0" * 12) + _record(67, words + table + units(13))
            + _record(67, "😀 last".encode("utf-16-le")))

    assert hwp_record_paragraphs(data) == ["Invented 칸", "😀 last"]


def test_a_file_that_cannot_be_read_beside_the_print_gives_no_paragraphs(tmp_path: Path) -> None:
    broken = tmp_path / "n.hwp"
    broken.write_bytes(b"not an ole file")

    assert file_paragraphs(broken) == []


def test_only_what_the_print_lacks_is_undrawn() -> None:
    printed = "Invented notice\nof the year\nBefore  table"

    assert undrawn(["Invented notice of the year", "Before table", "첫 칸 & 내용", "ab"],
                   printed) == ["첫 칸 & 내용"]


def test_a_private_use_bracket_the_renderer_draws_as_its_own_is_not_undrawn() -> None:
    # Hangul keeps its double brackets in the private-use area; the print shows 『 』.
    held = "\U000f0854Invented Programme\U000f0855 is announced, as follows."

    assert undrawn([held], "『Invented Programme』 is announced, as follows.") == []


def test_a_cell_printed_line_by_line_across_its_row_is_drawn() -> None:
    # A row is printed a line at a time across its cells, so a cell's lines come apart.
    printed = ("Invented grant for\nApplicants under\nnew firms in the\nseven years\n"
               "region\n- 4 -\nNo. Name Summary\nand their staff")

    assert undrawn(["Invented grant for new firms in the region",
                    "Applicants under seven years and their staff"], printed) == []


def test_a_cell_whose_middle_is_not_printed_is_undrawn() -> None:
    printed = "Invented grant for\nApplicants under\nseven years\nregion"

    assert undrawn(["Invented grant for new firms in the region"], printed) == [
        "Invented grant for new firms in the region"]


def _print(path: Path) -> Path:
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    document.new_page().insert_text((72, 72), "Invented notice of the year")
    document.save(path)  # type: ignore[no-untyped-call]
    return path


def test_undrawn_paragraphs_go_on_pages_after_the_print(tmp_path: Path) -> None:
    pdf = _print(tmp_path / "n.hwpx")

    kept = keep_undrawn(_hwpx(tmp_path / "source.hwpx"), pdf)

    with pymupdf.open(pdf) as document:  # type: ignore[no-untyped-call]
        assert document.page_count == 2
        added = document[1].get_text()
    assert kept == 3
    assert "첫 칸 & 내용" in added and "Before table" in added
    assert "Invented notice" not in added


def test_a_print_holding_every_paragraph_is_left_as_it_is(tmp_path: Path) -> None:
    pdf = _print(tmp_path / "n.hwpx")
    before = pdf.read_bytes()
    source = tmp_path / "s.hwpx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Contents/section0.xml", SECTION.split("<hp:p><hp:run><hp:t>Before")[0]
                         + "</hs:sec>")

    assert keep_undrawn(source, pdf) == 0
    assert pdf.read_bytes() == before


def test_a_paragraph_set_after_the_print_reads_back_with_its_middle_dot(tmp_path: Path) -> None:
    pdf = _print(tmp_path / "n.hwpx")
    source = tmp_path / "dot.hwpx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Contents/section0.xml", SECTION.split("<hp:p>")[0]
                         + "<hp:p><hp:run><hp:t>점검 항목ㆍ기준ㆍ주기</hp:t></hp:run></hp:p>"
                         + "</hs:sec>")

    assert keep_undrawn(source, pdf) == 1
    with pymupdf.open(pdf) as document:  # type: ignore[no-untyped-call]
        assert "점검 항목ㆍ기준ㆍ주기" in document[1].get_text()

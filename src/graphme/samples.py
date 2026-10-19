"""Sample documents to try the demo on before one's own: a made-up valve maker's specs,
procedure, work order, test record and improvement deck, written fresh each time they are
asked for. Every name, number and value in them is invented for the demo -- none comes from
a real document -- and they name one another by document number, so the graph has
something to join. Four of them carry files inside -- a Word file on a slide, a PDF in a
memo, a workbook attached to a PDF -- the way Office and PDF keep an attachment, so the
graph shows a document read out of another and cited from where it sat.

    python -m graphme.samples <folder>
"""
# ruff: noqa: E501
from __future__ import annotations

import io
import os
import re
import struct
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import IO

import pymupdf
from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import PROG_ID
from pptx.util import Inches

from graphme.application.file_titles import OFFICE_REL

COMPANY = "Garam Precision (fictional company)"
DAY = 24 * 60 * 60
WRITTEN = datetime(2026, 1, 5, 9, 0)
"""When each file says it was written. Fixed, with every zip entry's time, so the same samples
are the same bytes each time: the graph knows a document by its content, and samples asked
for again are then found there instead of read and put in a second time."""


def _docx(path: Path | IO[bytes], title: str, sections: list[tuple[str, list[str]]],
          table: list[list[str]] | None = None) -> None:
    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = WRITTEN
    doc.add_heading(title, level=0)
    doc.add_paragraph(f"{COMPANY} · Demo sample document")
    for heading, paragraphs in sections:
        doc.add_heading(heading, level=1)
        for text in paragraphs:
            doc.add_paragraph(text)
    if table:
        grid = doc.add_table(rows=len(table), cols=len(table[0]))
        grid.style = "Table Grid"
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    doc.save(str(path) if isinstance(path, Path) else path)


def _rows(sheet: Worksheet, rows: list[list[object]], *, start: int = 1,
          bold_head: bool = False) -> None:
    for r, row in enumerate(rows, start=start):
        for c, value in enumerate(row, start=1):
            cell = sheet.cell(r, c, value)
            if bold_head:
                cell.font = Font(bold=r == start)


def _spec(folder: Path) -> Path:
    path = folder / "GS-100_Valve Part Basic Specification.docx"
    _docx(path, "GS-100 Valve Part Basic Specification", [
        ("1. Scope", [
            "This specification sets the part dimensions and materials of the ball valves (nominal diameter 15 to 50) made by Garam Precision."]),
        ("2. Materials", ["The body is stainless steel STS316 and the ball is STS304.",
                     "The seat packing is PTFE, and the operating temperature is -20 ℃ to 180 ℃."]),
        ("3. Dimensional Tolerances", ["The body bore tolerance is ±0.05 mm and the ball roundness is 0.02 mm or less."]),
    ], table=[["Part", "Material", "Tolerance"], ["Body", "STS316", "±0.05 mm"],
              ["Ball", "STS304", "Roundness 0.02 mm"]])
    return path


def _test_spec(folder: Path) -> Path:
    path = folder / "GS-110_Leak Test Specification.docx"
    _docx(path, "GS-110 Leak Test Specification", [
        ("1. Purpose", ["This specification sets the leak-tightness criteria for valves made to GS-100 Valve Part Basic Specification."]),
        ("2. Test Pressure", ["The seat leak test pressure is 1.1 MPa and the body pressure test pressure is 1.5 MPa.",
                          "The pressure hold time for each is 60 seconds or more."]),
        ("3. Acceptance Criteria", [
            "A valve passes if there is no visible leakage during the test and the pressure drop is 0.01 MPa or less."]),
    ])
    return path


def _procedure(folder: Path) -> Path:
    path = folder / "QP-210_Leak Test Procedure.docx"
    _docx(path, "QP-210 Leak Test Procedure", [
        ("1. Preparation", ["Check that the calibration of the test rig pressure gauge is still valid.",
                     "Follow GS-110 Leak Test Specification for the test pressure and acceptance criteria."]),
        ("2. Test Sequence", ["Fix the valve in the jig and close the ball.",
                          "Raise the air pressure slowly to 1.1 MPa and hold it for 60 seconds.",
                          "Check for leaks around the seat with soapy water and record the pressure drop."]),
        ("3. Records", ["Record the results in the Leak Test Record by valve serial number.",
                     "For failed valves, measure the seat and ball dimensions again against GS-100."]),
    ])
    return path


def _work_order(folder: Path) -> Path:
    path = folder / "WO-2026-031_Valve Assembly Work Order.docx"
    _docx(path, "WO-2026-031 Valve Assembly Work Order", [
        ("1. Work Description", ["Assemble 200 ball valves, size 50A, and leak test every unit."]),
        ("2. Work Sequence", ["Clean the body and ball, then fit the seat packing.",
                          "Tighten the stem nut to 12 N·m.",
                          "Test each assembled valve according to QP-210 Leak Test Procedure."]),
        ("3. Cautions", ["Do not touch the seat packing with bare hands."]),
    ], table=[["Item", "Value"], ["Quantity", "200 units"], ["Due date", "2026-11-30"], ["Owner", "Assembly Team 2"]])
    return path


def _record(folder: Path) -> Path:
    path = folder / "Leak Test Record.xlsx"
    book = Workbook()
    book.properties.created = book.properties.modified = WRITTEN
    sheet = book.active
    sheet.title = "Test Record"
    sheet["A1"] = "Leak Test Record (per QP-210)"
    sheet["A1"].font = Font(bold=True, size=14)
    rows: list[list[object]] = [
            ["Serial No.", "Test pressure (MPa)", "Hold time (s)", "Pressure drop (MPa)", "Result"],
            ["V50-0001", 1.1, 60, 0.002, "Pass"],
            ["V50-0002", 1.1, 60, 0.004, "Pass"],
            ["V50-0003", 1.1, 60, 0.018, "Fail"],
            ["V50-0004", 1.1, 60, 0.003, "Pass"]]
    _rows(sheet, rows, start=3, bold_head=True)
    for column in "ABCDE":
        sheet.column_dimensions[column].width = 16
    book.save(path)
    return path


def _deck(folder: Path) -> Path:
    path = folder / "Leak Defect Improvement Report.pptx"
    deck = Presentation()
    deck.core_properties.created = deck.core_properties.modified = WRITTEN
    slides = [
        ("Leak Defect Improvement Report", [COMPANY, "Assembly Team 2 · Demo sample"]),
        ("Findings", ["Leak test failures occurred in the WO-2026-031 assembly batch",
                  "The pressure drop of the failed valve exceeded the acceptance criterion"]),
        ("Cause and Countermeasures", ["Cause: foreign matter got in when the seat packing was fitted",
                        "Countermeasure: work at a clean bench from cleaning until the packing is fitted",
                        "Retest at the same pressure and hold time as in QP-210 Leak Test Procedure"]),
    ]
    for title, lines in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = lines[0]
        for line in lines[1:]:
            body.add_paragraph().text = line
    deck.save(str(path))
    return path


# ---- documents carrying documents --------------------------------------------------------
REL = OFFICE_REL + "/"
_TYPES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          "bin": "application/vnd.openxmlformats-officedocument.oleObject",
          "png": "image/png"}
_PROGS = {".docx": PROG_ID.DOCX, ".xlsx": PROG_ID.XLSX, ".pptx": PROG_ID.PPTX}


def _inner_docx(title: str, lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    _docx(buffer, title, [("Contents", lines)])
    return _settle(buffer.getvalue())


def _inner_xlsx(title: str, rows: list[list[object]]) -> bytes:
    book = Workbook()
    book.properties.created = book.properties.modified = WRITTEN
    sheet = book.active
    sheet["A1"] = title
    sheet["A1"].font = Font(bold=True)
    _rows(sheet, rows, start=3)
    buffer = io.BytesIO()
    book.save(buffer)
    return _settle(buffer.getvalue())


def _inner_pptx(title: str, lines: list[str]) -> bytes:
    deck = Presentation()
    deck.core_properties.created = deck.core_properties.modified = WRITTEN
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = title
    slide.placeholders[1].text_frame.text = "\n".join(lines)
    buffer = io.BytesIO()
    deck.save(buffer)
    return _settle(buffer.getvalue())


def _pdf(title: str, lines: list[str]) -> pymupdf.Document:
    pdf = pymupdf.open()
    pdf.new_page().insert_textbox(pymupdf.Rect(56, 56, 540, 800),
                                  "\n\n".join([title, f"{COMPANY} · Demo sample document", *lines]),
                                  fontname="korea", fontsize=11)
    return pdf


def _pdf_bytes(pdf: pymupdf.Document) -> bytes:
    """The PDF's bytes, the same each time: pymupdf dates an attachment when it is added, and
    writes its name's UTF-8 bytes as they are, which a reader takes for Latin-1. Both are
    written again -- the date `WRITTEN`, the name in UTF-16, as the PDF standard has it."""
    stamp = WRITTEN.strftime("(D:%Y%m%d%H%M%S)")
    for xref in range(1, pdf.xref_length()):
        if pdf.xref_get_key(xref, "Type") == ("name", "/Filespec"):
            for key in ("F", "UF"):
                kind, value = pdf.xref_get_key(xref, key)
                if kind == "string":
                    name = value.encode("latin-1").decode("utf-8")
                    pdf.xref_set_key(xref, key, f"<FEFF{name.encode('utf-16-be').hex()}>")
        for key in ("Params/CreationDate", "Params/ModDate", "CreationDate", "M"):
            if pdf.xref_get_key(xref, key)[0] == "string":
                pdf.xref_set_key(xref, key, stamp)
    return pdf.tobytes(garbage=3, deflate=True, no_new_id=True)


def _icon() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), (60, 110, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def _packaged(name: str, data: bytes) -> bytes:
    """A file as Windows' OLE Packager keeps one in an Office document (`oleObject*.bin`): a
    compound file holding a `\\x01Ole10Native` stream -- the name in the code page, the file,
    then the name again in UTF-16."""
    ansi, temp = name.encode("cp949") + b"\0", f"C:\\Temp\\{name}".encode("cp949") + b"\0"
    wide_temp, wide = f"C:\\Temp\\{name}", name
    body = (struct.pack("<H", 2) + ansi + temp + struct.pack("<HH", 0, 3)
            + struct.pack("<I", len(temp)) + temp + struct.pack("<I", len(data)) + data
            + struct.pack("<I", len(wide_temp)) + wide_temp.encode("utf-16-le")
            + struct.pack("<I", len(wide)) + wide.encode("utf-16-le")
            + struct.pack("<I", len(wide_temp)) + wide_temp.encode("utf-16-le"))
    return _compound("\x01Ole10Native", struct.pack("<I", len(body)) + body)


def _compound(stream_name: str, data: bytes) -> bytes:
    """A compound file (MS-CFB, version 3) holding one stream, in regular sectors: padded past
    the 4 KiB below which a stream would live in the mini stream instead."""
    data = data.ljust(4096, b"\0")
    sectors = -(-len(data) // 512)
    if sectors > 126:
        raise ValueError("a sample attachment is kept under 64 KiB")
    end, free, none = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFF
    fat = [0xFFFFFFFD, end, *range(3, 2 + sectors), end]
    fat += [free] * (128 - len(fat))
    header = (bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 16
              + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, end, 0, end, 0)
              + struct.pack("<I", 0) + struct.pack("<I", free) * 108)

    def entry(name: str, kind: int, child: int, start: int, size: int) -> bytes:
        raw = name.encode("utf-16-le") + b"\0\0" if name else b""
        return (raw.ljust(64, b"\0") + struct.pack("<HBB", len(raw), kind, 1 if kind else 0)
                + struct.pack("<III", none, none, child) + b"\0" * 36
                + struct.pack("<II", start, size) + b"\0" * 4)

    directory = (entry("Root Entry", 5, 1, end, 0) + entry(stream_name, 2, none, 2, len(data))
                 + entry("", 0, none, 0, 0) * 2)
    return header + struct.pack("<128I", *fat) + directory + data.ljust(sectors * 512, b"\0")


def _carried(name: str, data: bytes) -> tuple[str, bytes, PROG_ID | str]:
    """How Office keeps a file: an OOXML file whole (`package`), anything else packaged."""
    suffix = Path(name).suffix
    if suffix in _PROGS:
        return "package", data, _PROGS[suffix]
    return "oleObject", _packaged(name, data), "Package"


def _rewritten(path: Path, edit: dict[str, bytes], types: list[str]) -> None:
    """The package at `path` with parts added or replaced, and `types` declared."""
    with zipfile.ZipFile(path) as old:
        declared = old.read("[Content_Types].xml").decode()
    for ext in types:
        if f'Extension="{ext}"' not in declared:
            declared = declared.replace(
                "<Default ",
                f'<Default Extension="{ext}" ContentType="{_TYPES[ext]}"/><Default ', 1)
    path.write_bytes(_settle(path.read_bytes(), edit | {"[Content_Types].xml": declared.encode()}))


SHARED_RULES = ("Shared Safety Rules.docx", [
    "Do not stand in front of the valve while test pressure is applied.",
    "Release the pressure, confirm 0 MPa, and only then unclamp the jig.",
    "These rules are attached to both the minutes and the meeting deck."])


def _meeting_deck(folder: Path) -> Path:
    """A deck with a Word file, a PDF and the shared rules each on a slide of its own."""
    path = folder / "Supplier Quality Meeting.pptx"
    deck = Presentation()
    deck.core_properties.created = deck.core_properties.modified = WRITTEN
    attachments = [
        ("IQ-310 Incoming Inspection Criteria (attached)", "IQ-310_Incoming Inspection Criteria.docx", _inner_docx(
            "IQ-310 Incoming Inspection Criteria", [
                "Balls delivered by suppliers must meet the roundness of 0.02 mm in GS-100 Valve Part Basic Specification.",
                "Sample 5 pieces from each lot and measure roundness and surface roughness.",
                "If even one piece is out of tolerance, the whole lot is returned."])),
        ("Defect Photo Report (attached)", "Defect Photo Report.pdf", _pdf_bytes(_pdf("Defect Photo Report", [
            "A foreign particle 0.3 mm in diameter was found on the seat packing surface of V50-0003.",
            "Particles appeared only on valves assembled on days when the washing tank filter was past its replacement interval.",
            "The washing tank filter replacement interval is shortened from 2 weeks to 1 week."]))),
        ("Shared Safety Rules (attached)", SHARED_RULES[0], _inner_docx("Shared Safety Rules", SHARED_RULES[1])),
    ]
    title = deck.slides.add_slide(deck.slide_layouts[0])
    title.shapes.title.text = "Supplier Quality Meeting"
    title.placeholders[1].text = f"{COMPANY} · Demo sample with attached files"
    for heading, name, data in attachments:
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = heading
        kind, blob, prog = _carried(name, data)
        slide.shapes.add_ole_object(io.BytesIO(blob), prog, Inches(1), Inches(2), icon_file=(
            io.BytesIO(_icon()) if kind == "oleObject" else None))
    deck.save(str(path))
    return path


def _minutes(folder: Path) -> Path:
    """A memo carrying a workbook, a PDF, a deck and the shared rules as icons."""
    path = folder / "Equipment Inspection Minutes.docx"
    _docx(path, "Equipment Inspection Minutes", [
        ("1. Agenda", ["Covers the leak tester inspection results, pressure gauge calibration and equipment improvements for next quarter."]),
        ("2. Attachments", ["The icons below are the files reviewed together at the meeting."]),
    ])
    attachments = [
        ("Inspection Checklist.xlsx", _inner_xlsx("Leak Tester Inspection Checklist", [
            ["Item", "Criterion", "Result"], ["Pressure gauge zero point", "±0.005 MPa", "OK"],
            ["Leak detection sensor", "Operating", "OK"], ["Hose cracks", "None", "Replacement needed"]])),
        ("Pressure Gauge Calibration Certificate.pdf", _pdf_bytes(_pdf("Pressure Gauge Calibration Certificate", [
            "Calibrated item: leak tester pressure gauge PG-07", "Calibration result: error of +0.003 MPa at 1.1 MPa",
            "Next calibration due: 2026-07-05"]))),
        ("Equipment Improvement Plan.pptx", _inner_pptx("Equipment Improvement Plan", [
            "Replace the leak tester hose with a high-pressure one", "Add the hose to the equipment inspection items of QP-210 Leak Test Procedure"])),
        (SHARED_RULES[0], _inner_docx("Shared Safety Rules", SHARED_RULES[1])),
    ]
    with zipfile.ZipFile(path) as old:
        document = old.read("word/document.xml").decode()
        rels = old.read("word/_rels/document.xml.rels").decode()
    edit = {"word/media/attachment_icon.png": _icon()}
    links = [f'<Relationship Id="rIdIcon" Type="{REL}image" Target="media/attachment_icon.png"/>']
    objects = ""
    for index, (name, data) in enumerate(attachments, start=1):
        kind, blob, prog = _carried(name, data)
        suffix = ".bin" if kind == "oleObject" else Path(name).suffix
        part = f"{'oleObject' if kind == 'oleObject' else 'attachment'}{index}{suffix}"
        edit[f"word/embeddings/{part}"] = blob
        links.append(f'<Relationship Id="rIdAt{index}" Type="{REL}{kind}" '
                     f'Target="embeddings/{part}"/>')
        shape = f"_x0000_i10{index:02d}"
        objects += (f'<w:p><w:r><w:object w:dxaOrig="960" w:dyaOrig="960"><v:shape id="{shape}" '
                    'type="#_x0000_t75" style="width:48pt;height:48pt" o:ole=""><v:imagedata '
                    f'r:id="rIdIcon" o:title=""/></v:shape><o:OLEObject Type="Embed" '
                    f'ProgID="{getattr(prog, "progId", prog)}" ShapeID="{shape}" DrawAspect="Icon" '
                    f'ObjectID="_17000000{index:02d}" r:id="rIdAt{index}"/></w:object></w:r></w:p>')
    edit["word/document.xml"] = document.replace("<w:sectPr", objects + "<w:sectPr", 1).encode()
    edit["word/_rels/document.xml.rels"] = rels.replace(
        "</Relationships>", "".join(links) + "</Relationships>").encode()
    _rewritten(path, edit, ["docx", "xlsx", "pptx", "bin", "png"])
    return path


def _price_book(folder: Path) -> Path:
    """A workbook whose second sheet carries the memo the prices were agreed in."""
    path = folder / "Parts Price List.xlsx"
    book = Workbook()
    book.properties.created = book.properties.modified = WRITTEN
    prices = book.active
    prices.title = "Prices"
    _rows(prices, [["Part", "Material", "Unit price (KRW)"], ["Body 50A", "STS316", 18500],
                   ["Ball 50A", "STS304", 7200], ["Seat packing", "PTFE", 900]])
    book.create_sheet("Negotiation")["A1"] = "The price negotiation memo is attached."
    book.save(path)
    memo = _inner_docx("Price Negotiation Memo", [
        "Because of the STS316 raw material price increase, the Body 50A unit price is raised to 18,500 won.",
        "The unit prices of the ball and seat packing are unchanged.", "This applies to orders placed after WO-2026-031."])
    with zipfile.ZipFile(path) as old:
        sheet = old.read("xl/worksheets/sheet2.xml").decode()
    if "xmlns:r=" not in sheet:
        sheet = sheet.replace("<worksheet ", f'<worksheet xmlns:r="{REL[:-1]}" ', 1)
    _rewritten(path, {
        "xl/embeddings/Microsoft_Word_Document.docx": memo,
        "xl/worksheets/_rels/sheet2.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns='
            '"http://schemas.openxmlformats.org/package/2006/relationships"><Relationship '
            f'Id="rId1" Type="{REL}package" Target="../embeddings/Microsoft_Word_Document.docx"/>'
            "</Relationships>").encode(),
        "xl/worksheets/sheet2.xml": sheet.replace(
            "</worksheet>", '<oleObjects><oleObject progId="Word.Document.12" shapeId="1025" '
            'r:id="rId1"/></oleObjects></worksheet>').encode(),
    }, ["docx"])
    return path


def _shipping_report(folder: Path) -> Path:
    """A PDF with a workbook attached on its page and a deck attached to the whole file."""
    path = folder / "Shipping Inspection Report.pdf"
    pdf = _pdf("Shipping Inspection Report", [
        "The shipping inspection of the 200 units from the WO-2026-031 assembly batch is complete.",
        "Results for each inspection item are in the checklist file attached to this page.",
        "The shipping briefing deck is attached to this report file."])
    pdf[0].add_file_annot(pymupdf.Point(60, 300), _inner_xlsx("Shipping Inspection Checklist", [
        ["Item", "Criterion", "Result"], ["Appearance", "No scratches", "OK"],
        ["Leak tightness", "GS-110 acceptance criteria", "200 units passed"], ["Packaging", "Rust-proof paper enclosed", "OK"]]),
        "Shipping Inspection Checklist.xlsx")
    pdf.embfile_add("Shipping Briefing.pptx", _inner_pptx("Shipping Briefing", [
        "Shipped quantity 200 units, due date 2026-11-30 met", "Rust-proof paper specification for packaging changes from the next lot"]),
        filename="Shipping Briefing.pptx")
    path.write_bytes(_pdf_bytes(pdf))
    return path


WRITERS = (_spec, _test_spec, _procedure, _work_order, _record, _deck,
           _meeting_deck, _minutes, _price_book, _shipping_report)


def _settle(data: bytes, edit: dict[str, bytes] | None = None) -> bytes:
    """An Office file's zip written again with one time on every entry -- and in the file's
    own record of when it was changed, which openpyxl sets to the moment it saves. Parts in
    `edit` are added or replaced in the same pass."""
    stamp = WRITTEN.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    with zipfile.ZipFile(io.BytesIO(data)) as old:
        infos = {info.filename: info for info in old.infolist()}
        entries = {name: old.read(info) for name, info in infos.items()}
    entries |= edit or {}
    if "docProps/core.xml" in entries:
        entries["docProps/core.xml"] = re.sub(
            rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + stamp, entries["docProps/core.xml"])
    settled = io.BytesIO()
    with zipfile.ZipFile(settled, "w", zipfile.ZIP_DEFLATED) as new:
        for name, part in entries.items():
            info = infos.get(name) or zipfile.ZipInfo(name)
            info.date_time = WRITTEN.timetuple()[:6]
            new.writestr(info, part, zipfile.ZIP_DEFLATED)
    return settled.getvalue()


def _settled(path: Path) -> None:
    if path.suffix == ".pdf":
        return
    with zipfile.ZipFile(path) as package:  # one `_rewritten` made already is not made again
        if all(info.date_time == WRITTEN.timetuple()[:6] for info in package.infolist()):
            return
    path.write_bytes(_settle(path.read_bytes()))


def write_samples(folder: Path) -> list[Path]:
    """The sample documents, written into `folder`; each dated a few days apart, as a real
    folder's files would be."""
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    written = []
    for age, write in enumerate(reversed(WRITERS)):
        path = write(folder)
        _settled(path)
        when = now - (age * 3 + 1) * DAY
        os.utime(path, (when, when))
        written.append(path)
    return sorted(written)


if __name__ == "__main__":
    # A Windows pipe writes in the ANSI code page, which has no Hangul for the file names.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    for path in write_samples(Path(sys.argv[1])):
        print(path)

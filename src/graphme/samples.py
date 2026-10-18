"""Sample documents to try the demo on before one's own: a made-up valve maker's specs,
procedure, work order, test record and improvement deck, written fresh each time they are
asked for. Every name, number and value in them is invented for the demo -- none comes from
a real document -- and they name one another by document number, so the graph has
something to join. Four of them carry files inside -- a Word file on a slide, a PDF in a
memo, a workbook attached to a PDF -- the way Office and PDF keep an attachment, so the
graph shows a document read out of another and cited from where it sat.

    python -m graphme.samples <folder>
"""
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

COMPANY = "가람정밀 (가상 회사)"
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
    doc.add_paragraph(f"{COMPANY} · 데모용 샘플 문서")
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
    path = folder / "GS-100_밸브 부품 기본 규격서.docx"
    _docx(path, "GS-100 밸브 부품 기본 규격서", [
        ("1. 적용 범위", [
            "이 규격은 가람정밀이 만드는 볼 밸브(호칭 지름 15~50)의 부품 치수와 재질을 정한다."]),
        ("2. 재질", ["몸체는 스테인리스강 STS316, 볼은 STS304를 쓴다.",
                     "시트 패킹은 PTFE로 하고, 사용 온도는 -20 ℃ ~ 180 ℃로 한다."]),
        ("3. 치수 공차", ["몸체 내경 공차는 ±0.05 mm, 볼 진원도는 0.02 mm 이하로 한다."]),
    ], table=[["부품", "재질", "공차"], ["몸체", "STS316", "±0.05 mm"],
              ["볼", "STS304", "진원도 0.02 mm"]])
    return path


def _test_spec(folder: Path) -> Path:
    path = folder / "GS-110_기밀 시험 규격서.docx"
    _docx(path, "GS-110 기밀 시험 규격서", [
        ("1. 목적", ["GS-100 밸브 부품 기본 규격서에 따라 만든 밸브의 기밀 성능 기준을 정한다."]),
        ("2. 시험 압력", ["시트 기밀 시험 압력은 1.1 MPa, 몸체 내압 시험 압력은 1.5 MPa로 한다.",
                          "압력 유지 시간은 각각 60초 이상으로 한다."]),
        ("3. 판정 기준", [
            "시험 중 눈에 보이는 누설이 없고, 압력 강하가 0.01 MPa 이하이면 합격이다."]),
    ])
    return path


def _procedure(folder: Path) -> Path:
    path = folder / "QP-210_기밀 시험 절차서.docx"
    _docx(path, "QP-210 기밀 시험 절차서", [
        ("1. 준비", ["시험 장치의 압력계 교정 유효기간을 확인한다.",
                     "시험 압력과 판정 기준은 GS-110 기밀 시험 규격서를 따른다."]),
        ("2. 시험 순서", ["밸브를 지그에 고정하고 볼을 닫는다.",
                          "공기를 1.1 MPa까지 천천히 올린 뒤 60초간 유지한다.",
                          "비눗물로 시트 주변 누설을 확인하고 압력 강하를 기록한다."]),
        ("3. 기록", ["결과는 기밀 시험 기록표에 밸브 일련번호별로 남긴다.",
                     "불합격품은 GS-100 기준으로 시트와 볼 치수를 다시 측정한다."]),
    ])
    return path


def _work_order(folder: Path) -> Path:
    path = folder / "WO-2026-031_밸브 조립 작업지시서.docx"
    _docx(path, "WO-2026-031 밸브 조립 작업지시서", [
        ("1. 작업 내용", ["볼 밸브 50A 200대를 조립하고 전수 기밀 시험을 한다."]),
        ("2. 작업 순서", ["몸체와 볼을 세척한 뒤 시트 패킹을 끼운다.",
                          "스템 너트를 12 N·m로 조인다.",
                          "조립이 끝난 밸브는 QP-210 기밀 시험 절차서에 따라 시험한다."]),
        ("3. 주의", ["시트 패킹은 맨손으로 만지지 않는다."]),
    ], table=[["항목", "값"], ["수량", "200대"], ["납기", "2026-11-30"], ["담당", "조립 2팀"]])
    return path


def _record(folder: Path) -> Path:
    path = folder / "기밀 시험 기록표.xlsx"
    book = Workbook()
    book.properties.created = book.properties.modified = WRITTEN
    sheet = book.active
    sheet.title = "시험 기록"
    sheet["A1"] = "기밀 시험 기록표 (QP-210에 따름)"
    sheet["A1"].font = Font(bold=True, size=14)
    rows: list[list[object]] = [
            ["일련번호", "시험 압력(MPa)", "유지 시간(초)", "압력 강하(MPa)", "판정"],
            ["V50-0001", 1.1, 60, 0.002, "합격"],
            ["V50-0002", 1.1, 60, 0.004, "합격"],
            ["V50-0003", 1.1, 60, 0.018, "불합격"],
            ["V50-0004", 1.1, 60, 0.003, "합격"]]
    _rows(sheet, rows, start=3, bold_head=True)
    for column in "ABCDE":
        sheet.column_dimensions[column].width = 16
    book.save(path)
    return path


def _deck(folder: Path) -> Path:
    path = folder / "기밀 불량 개선 보고.pptx"
    deck = Presentation()
    deck.core_properties.created = deck.core_properties.modified = WRITTEN
    slides = [
        ("기밀 불량 개선 보고", [COMPANY, "조립 2팀 · 데모용 샘플"]),
        ("현상", ["WO-2026-031 조립분에서 기밀 시험 불합격 발생",
                  "불합격 밸브의 압력 강하가 판정 기준을 넘음"]),
        ("원인과 대책", ["원인: 시트 패킹 삽입 시 이물 혼입",
                        "대책: 세척 후 패킹 삽입까지 클린 벤치에서 작업",
                        "재시험은 QP-210 절차와 같은 압력·유지 시간으로 실시"]),
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
    _docx(buffer, title, [("내용", lines)])
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
                                  "\n\n".join([title, f"{COMPANY} · 데모용 샘플 문서", *lines]),
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


SHARED_RULES = ("공통 안전 수칙.docx", [
    "시험 압력을 거는 동안 밸브 정면에 서지 않는다.",
    "압력을 뺀 뒤 0 MPa를 확인하고 나서 지그를 푼다.",
    "이 수칙은 회의록과 회의 자료에 함께 첨부한다."])


def _meeting_deck(folder: Path) -> Path:
    """A deck with a Word file, a PDF and the shared rules each on a slide of its own."""
    path = folder / "협력사 품질 회의.pptx"
    deck = Presentation()
    deck.core_properties.created = deck.core_properties.modified = WRITTEN
    attachments = [
        ("IQ-310 입고 검사 기준 (첨부)", "IQ-310_입고 검사 기준.docx", _inner_docx(
            "IQ-310 입고 검사 기준", [
                "협력사가 납품하는 볼은 GS-100 밸브 부품 기본 규격서의 진원도 0.02 mm를 따른다.",
                "로트마다 5개를 뽑아 진원도와 표면 거칠기를 잰다.",
                "한 개라도 벗어나면 로트 전체를 돌려보낸다."])),
        ("불량 사진 보고 (첨부)", "불량 사진 보고.pdf", _pdf_bytes(_pdf("불량 사진 보고", [
            "V50-0003 시트 패킹 표면에서 지름 0.3 mm 이물이 발견되었다.",
            "이물은 세척조 필터 교체 주기를 넘긴 날 조립된 밸브에서만 나왔다.",
            "세척조 필터 교체 주기를 2주에서 1주로 줄인다."]))),
        ("공통 안전 수칙 (첨부)", SHARED_RULES[0], _inner_docx("공통 안전 수칙", SHARED_RULES[1])),
    ]
    title = deck.slides.add_slide(deck.slide_layouts[0])
    title.shapes.title.text = "협력사 품질 회의"
    title.placeholders[1].text = f"{COMPANY} · 첨부 파일이 든 데모용 샘플"
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
    path = folder / "설비 점검 회의록.docx"
    _docx(path, "설비 점검 회의록", [
        ("1. 안건", ["기밀 시험기 점검 결과와 압력계 교정, 다음 분기 설비 개선을 다룬다."]),
        ("2. 첨부", ["아래 아이콘은 회의에서 함께 본 파일이다."]),
    ])
    attachments = [
        ("점검 체크리스트.xlsx", _inner_xlsx("기밀 시험기 점검 체크리스트", [
            ["항목", "기준", "결과"], ["압력계 영점", "±0.005 MPa", "적합"],
            ["누설 감지 센서", "작동", "적합"], ["호스 균열", "없음", "교체 필요"]])),
        ("압력계 교정 성적서.pdf", _pdf_bytes(_pdf("압력계 교정 성적서", [
            "교정 대상: 기밀 시험기 압력계 PG-07", "교정 결과: 1.1 MPa에서 오차 +0.003 MPa",
            "다음 교정 예정일: 2026-07-05"]))),
        ("설비 개선 계획.pptx", _inner_pptx("설비 개선 계획", [
            "기밀 시험기 호스를 고압용으로 교체", "QP-210 절차서의 장비 점검 항목에 호스를 추가"])),
        (SHARED_RULES[0], _inner_docx("공통 안전 수칙", SHARED_RULES[1])),
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
    path = folder / "부품 단가표.xlsx"
    book = Workbook()
    book.properties.created = book.properties.modified = WRITTEN
    prices = book.active
    prices.title = "단가"
    _rows(prices, [["부품", "재질", "단가(원)"], ["몸체 50A", "STS316", 18500],
                   ["볼 50A", "STS304", 7200], ["시트 패킹", "PTFE", 900]])
    book.create_sheet("협의")["A1"] = "단가 협의 메모를 첨부한다."
    book.save(path)
    memo = _inner_docx("단가 협의 메모", [
        "STS316 원자재 가격 인상으로 몸체 50A 단가를 18,500원으로 올린다.",
        "볼과 시트 패킹 단가는 그대로 둔다.", "적용은 WO-2026-031 이후 발주분부터 한다."])
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
    path = folder / "출하 검사 보고서.pdf"
    pdf = _pdf("출하 검사 보고서", [
        "WO-2026-031 조립분 200대의 출하 검사를 마쳤다.",
        "검사 항목별 결과는 이 쪽에 붙인 체크리스트 파일에 있다.",
        "출하 브리핑 자료는 이 보고서 파일에 함께 붙였다."])
    pdf[0].add_file_annot(pymupdf.Point(60, 300), _inner_xlsx("출하 검사 체크리스트", [
        ["항목", "기준", "결과"], ["외관", "흠집 없음", "적합"],
        ["기밀", "GS-110 판정 기준", "200대 합격"], ["포장", "방청지 동봉", "적합"]]),
        "출하 검사 체크리스트.xlsx")
    pdf.embfile_add("출하 브리핑.pptx", _inner_pptx("출하 브리핑", [
        "출하 수량 200대, 납기 2026-11-30 준수", "다음 로트부터 포장 방청지 규격 변경"]),
        filename="출하 브리핑.pptx")
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
    for path in write_samples(Path(sys.argv[1])):
        print(path)

"""Which PDF pages are scans, judged from the file alone, and what of a scan was read.

A scan is a picture of a page: the file holds no words for it. The page-reading model reads
the pages these rules pick (`PDFExtractor`); what it reads is kept where it lies on the scan.
"""
from __future__ import annotations

from collections.abc import Sequence
from math import ceil, sqrt
from typing import Protocol

import numpy
import pymupdf

from graphme.adapters.chat import MODEL_IMAGE_PIXELS
from graphme.domain.enums import NodeType

SCAN_COVERAGE = 0.5
"""How much of a page its pictures have to cover for the page to be a scan: more picture
than not. A scan is laid on the page inside whatever margins the scanner or the filer left
-- an inch of them leaves it under two thirds of a letter page -- and a scanner can store
one page as many strips, so the pictures count together. A page of photographs covers as
much; whether a candidate is a page of writing is the model's to say, as it is for a page
whose only content is one picture, whatever its size."""
STAMP_LINES = 3
"""How many lines of the file's own words may lie over the pictures of a scan: a filing
header, a page number, a received stamp. More than that is text printed over a picture."""
SCAN_GRID = 40
"""Samples per side of the page at which the pictures' cover is measured -- strips meet edge
to edge, and a sample is exact enough for a share this coarse."""
SCAN_DPI = 150
SCANNED_TYPES = {"text": NodeType.TEXT_CHUNK, "table": NodeType.TABLE,
                 "picture": NodeType.IMAGE, "chart": NodeType.CHART}
UNREADABLE = "[?]"
"""What the model writes for a word it cannot make out. A page read as nothing else was not
read at all."""


class _Placed(Protocol):
    @property
    def bbox(self) -> tuple[float, float, float, float]: ...

    @property
    def text(self) -> str | None: ...


def is_scan(page: pymupdf.Page) -> bool:
    """A page that is a picture of a page, judged from the file alone (`SCAN_COVERAGE`,
    `STAMP_LINES`)."""
    # Everything is judged in the page as it is shown, as units are placed (`page_text`):
    # pymupdf reports pictures and words on a page with /Rotate in the unturned page.
    area = page.rect
    turn = page.rotation_matrix
    stated = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]  # type: ignore[no-untyped-call]
    pictures = [rect for box in stated if not (rect := box * turn & area).is_empty]
    if not pictures:
        return False
    words = [(pymupdf.Rect(word[:4]) * turn, word[5:7])  # type: ignore[no-untyped-call]
             for word in page.get_text("words") if word[4].strip()]  # type: ignore[no-untyped-call]
    if len(pictures) == 1 and not words:
        return True
    stamped = {line for box, line in words if any(p.intersects(box) for p in pictures)}
    if len(stamped) > STAMP_LINES:
        return False
    # Pictures together cover no more than their areas add up to: most pages stop here.
    if sum(picture.get_area() for picture in pictures) < SCAN_COVERAGE * area.get_area():
        return False
    # The sample points each picture covers, marked once per picture: a scan stored as a
    # hundred strips is a hundred slices, not a hundred tests at every point.
    covered = numpy.zeros((SCAN_GRID, SCAN_GRID), dtype=bool)
    step_x, step_y = area.width / SCAN_GRID, area.height / SCAN_GRID
    for picture in pictures:
        # Point i is at (i + 0.5) steps; the picture takes those from its left edge to before
        # its right one, as pymupdf's `Point in Rect` does.
        left, right = (ceil((edge - area.x0) / step_x - 0.5) for edge in (picture.x0, picture.x1))
        top, bottom = (ceil((edge - area.y0) / step_y - 0.5) for edge in (picture.y0, picture.y1))
        covered[max(0, top):max(0, bottom), max(0, left):max(0, right)] = True
    return int(covered.sum()) >= SCAN_COVERAGE * SCAN_GRID * SCAN_GRID


def scan_dpi(page: pymupdf.Page) -> int:
    """The resolution a scanned page is drawn at for the model: as many pixels as it reads
    (`MODEL_IMAGE_PIXELS`) and no more -- a larger picture is scaled down before it is read,
    so drawing it larger only costs time. Never past SCAN_DPI."""
    inches = (page.rect.width / 72) * (page.rect.height / 72)
    return max(1, min(SCAN_DPI, int(sqrt(MODEL_IMAGE_PIXELS / max(inches, 1e-6)))))


def centred_in(bbox: tuple[float, float, float, float], units: Sequence[_Placed]) -> bool:
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(u.bbox[0] <= x <= u.bbox[2] and u.bbox[1] <= y <= u.bbox[3] for u in units)


def readable(read: Sequence[_Placed]) -> bool:
    """Something read, and not only marks for what could not be (`UNREADABLE`)."""
    return any(character.isalnum()
               for unit in read for character in (unit.text or "").replace(UNREADABLE, ""))

"""A picture placed by a start cell and a size (`oneCellAnchor`) with no position of its own
in its shape properties -- how openpyxl writes one -- is drawn by the viewer itself where its
anchor says (upstream d7f60b865); it used to vanish without an error. Drawn with the viewer
bundle; invented workbooks."""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from PIL import Image

import graphme
from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer
from graphme.domain.enums import DocumentFormat

RED = (200, 30, 30)


def _renderer(tmp_path: Path) -> BrowserOfficeRenderer:
    settings = graphme.load_settings({
        **{n: v for n, v in os.environ.items()
           if n in ("GRAPHME_VIEWER_DIR", "GRAPHME_CHROME_EXECUTABLE")},
        "GRAPHME_PROFILE": "test", "GRAPHME_DATA_DIR": str(tmp_path / "data")})
    if not (settings.chrome_executable and (settings.viewer_directory / "xlsx").is_dir()):
        pytest.skip("drawn by Chrome with the viewer bundle (README 0-1)")
    return BrowserOfficeRenderer(viewer_directory=settings.viewer_directory,
                                 chrome_executable=settings.chrome_executable)


def test_a_one_cell_picture_with_no_position_of_its_own_is_drawn(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    book = Workbook()
    sheet = book.active
    sheet["A1"] = "Invented inspection photo"
    picture = io.BytesIO()
    Image.new("RGB", (160, 90), RED).save(picture, "PNG")
    sheet.add_image(SheetImage(picture), "B3")  # openpyxl: a oneCellAnchor, no <a:xfrm>
    path = tmp_path / "Invented photo sheet.xlsx"
    book.save(path)

    page = renderer.render(path, DocumentFormat.XLSX)[0]

    image = Image.open(io.BytesIO(page.png)).convert("RGB")
    red = sum(1 for r, g, b in image.getdata() if r > 180 and g < 60 and b < 60)
    scale = page.device_pixel_ratio or 1.0
    assert red > 0.8 * 160 * 90 * scale * scale  # the whole picture, at the drawn scale

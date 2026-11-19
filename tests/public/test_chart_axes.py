"""A combo whose columns sit on a deleted secondary category axis -- written by Excel at the
bottom beside the primary one -- is drawn by the viewer itself: two category axes on one side
were painted "Unsupported chart" until upstream resolved them (#1984). Drawn with the viewer
bundle; invented charts."""
from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

import graphme
from graphme.adapters.rendering.browser_office import BrowserOfficeRenderer
from graphme.domain.enums import DocumentFormat

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May"]


def _renderer(tmp_path: Path) -> BrowserOfficeRenderer:
    settings = graphme.load_settings({
        **{n: v for n, v in os.environ.items()
           if n in ("GRAPHME_VIEWER_DIR", "GRAPHME_CHROME_EXECUTABLE")},
        "GRAPHME_PROFILE": "test", "GRAPHME_DATA_DIR": str(tmp_path / "data")})
    if not (settings.chrome_executable and (settings.viewer_directory / "dist").is_dir()):
        pytest.skip("drawn by Chrome with the viewer bundle (README 0-1)")
    return BrowserOfficeRenderer(viewer_directory=settings.viewer_directory,
                                 chrome_executable=settings.chrome_executable)


def _combo_on_a_hidden_axis(path: Path) -> Path:
    """Columns on their own deleted category axis at the bottom and a value axis on the right,
    a line on the primary pair: what Excel writes for 'columns on the secondary axis'."""
    data = CategoryChartData()
    data.categories = MONTHS
    data.add_series("Lots", (120, 80, 95, 110, 130))
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(8),
                           Inches(5), data)
    deck.save(path)

    def hidden(xml: str) -> str:
        ids = re.search(r'<c:barChart>.*?<c:axId val="(-?\d+)"/>\s*<c:axId val="(-?\d+)"/>',
                        xml, re.S)
        assert ids is not None
        points = "".join(f'<c:pt idx="{i}"><c:v>{v}</c:v></c:pt>'
                         for i, v in enumerate((4, 0, 2, 5, 3)))
        line = ('<c:lineChart><c:grouping val="standard"/><c:ser><c:idx val="1"/><c:order val="1"/>'
                f'<c:val><c:numLit><c:ptCount val="5"/>{points}</c:numLit></c:val></c:ser>'
                f'<c:axId val="{ids[1]}"/><c:axId val="{ids[2]}"/></c:lineChart>')
        xml = xml.replace(f'<c:axId val="{ids[1]}"/><c:axId val="{ids[2]}"/></c:barChart>',
                          '<c:axId val="30"/><c:axId val="40"/></c:barChart>' + line, 1)
        return xml.replace("</c:plotArea>", (
            '<c:catAx><c:axId val="30"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            '<c:delete val="1"/><c:axPos val="b"/><c:crossAx val="40"/></c:catAx>'
            '<c:valAx><c:axId val="40"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            '<c:delete val="0"/><c:axPos val="r"/><c:crossAx val="30"/><c:crosses val="max"/>'
            '</c:valAx></c:plotArea>'), 1)

    original = io.BytesIO(path.read_bytes())
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            body = src.read(item)
            if re.fullmatch(r"ppt/charts/chart\d*\.xml", item.filename):
                body = hidden(body.decode()).encode()
            out.writestr(item, body)
    return path


def test_columns_on_a_hidden_bottom_category_axis_are_drawn(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    deck = _combo_on_a_hidden_axis(tmp_path / "Invented combo.pptx")

    page = renderer.render(deck, DocumentFormat.PPTX)[0]

    # Drawn, the columns cover a good part of the slide; painted "Unsupported chart", the
    # slide stays white but for one small grey line of text.
    image = Image.open(io.BytesIO(page.png)).convert("L")
    inked = sum(1 for value in image.getdata() if value < 200)
    assert inked > 0.05 * image.width * image.height

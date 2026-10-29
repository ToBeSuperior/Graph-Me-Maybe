"""A combo whose columns sit on a deleted secondary category axis -- written by Excel at the
bottom beside the primary one -- is drawn by the published viewer only once the hidden axis
is folded into the primary one: two category axes on one side painted "Unsupported chart".
Invented charts."""
from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from graphme.adapters.rendering.chart_axes import hidden_axes_merged

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"c": C}


def _chart(hidden_pos: str = "b") -> str:
    return f"""<c:chartSpace xmlns:c="{C}"><c:chart><c:plotArea>
      <c:lineChart><c:ser><c:idx val="1"/></c:ser><c:axId val="10"/><c:axId val="20"/></c:lineChart>
      <c:barChart><c:barDir val="col"/><c:ser><c:idx val="0"/></c:ser>
        <c:axId val="30"/><c:axId val="40"/></c:barChart>
      <c:catAx><c:axId val="10"/><c:axPos val="b"/><c:crossAx val="20"/></c:catAx>
      <c:valAx><c:axId val="20"/><c:axPos val="l"/><c:crossAx val="10"/></c:valAx>
      <c:catAx><c:axId val="30"/><c:delete val="1"/><c:axPos val="{hidden_pos}"/>
        <c:crossAx val="40"/></c:catAx>
      <c:valAx><c:axId val="40"/><c:axPos val="r"/><c:crossAx val="30"/>
        <c:crosses val="max"/></c:valAx>
    </c:plotArea></c:chart></c:chartSpace>"""


def _deck(path: Path, chart: str) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<slide/>")
        archive.writestr("ppt/charts/chart1.xml", chart)
    return path


def _axes(path: Path) -> tuple[list[list[str]], dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("ppt/charts/chart1.xml"))
    plot = root.find(".//c:plotArea", NS)
    groups = [[a.get("val") for a in g.findall("c:axId", NS)]
              for g in (plot.find("c:lineChart", NS), plot.find("c:barChart", NS))]
    crossings = {a.find("c:axId", NS).get("val"): a.find("c:crossAx", NS).get("val")
                 for a in [*plot.findall("c:catAx", NS), *plot.findall("c:valAx", NS)]}
    return groups, crossings


def test_columns_on_a_hidden_bottom_category_axis_are_drawn_on_the_primary_one(
    tmp_path: Path,
) -> None:
    source = _deck(tmp_path / "deck.pptx", _chart())

    drawn = hidden_axes_merged(source, tmp_path / "work")

    assert drawn != source
    groups, crossings = _axes(drawn)
    assert groups == [["10", "20"], ["10", "40"]]  # the columns on the primary categories
    assert crossings == {"10": "20", "20": "10", "40": "10"}  # the hidden axis gone
    assert _axes(source)[0] == [["10", "20"], ["30", "40"]]  # the original untouched


def test_a_hidden_axis_on_a_side_of_its_own_is_left_as_it_is(tmp_path: Path) -> None:
    source = _deck(tmp_path / "deck.pptx", _chart(hidden_pos="t"))

    assert hidden_axes_merged(source, tmp_path / "work") == source


def test_a_file_with_no_chart_or_no_office_file_is_drawn_from_itself(tmp_path: Path) -> None:
    plain = _deck(tmp_path / "deck.pptx", "<c:chartSpace xmlns:c='" + C + "'/>")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    assert hidden_axes_merged(plain, tmp_path / "work") == plain
    assert hidden_axes_merged(pdf, tmp_path / "work") == pdf

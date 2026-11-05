"""A part of a figure keeps no crop of its own: every reader sends the figure's, which holds the
part, and the part's own crop was kept for nothing. The app shows a part by its figure's crop.
Invented report."""
from __future__ import annotations

from graphme.application.figure_groups import widen_figures
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import HierarchyFigureGroup
from graphme.server.state import kept_from
from tests.public.test_table_parts_stay_in_its_frame import BOXES, _Placer, _report, _Store


def test_a_part_drops_its_own_crop_when_it_joins_a_figure() -> None:
    report = _report()
    report = report.model_copy(update={"nodes": tuple(
        n.model_copy(update={"metadata": {**n.metadata, CROP_ASSET_KEY: f"own-{n.id}"}})
        if n.id in ("grid", "icon") else n for n in report.nodes)})

    widened = widen_figures(
        report, figures=(HierarchyFigureGroup(figure_id="grid", member_ids=("icon",)),),
        placers={1: _Placer()}, page_of={key: 1 for key in BOXES},
        crop=lambda page, box: b"png", asset_store=_Store())

    nodes = {n.id: n for n in widened.nodes}
    assert nodes["icon"].metadata.get("part_of_figure") == "grid"
    assert CROP_ASSET_KEY not in nodes["icon"].metadata
    assert nodes["grid"].metadata[CROP_ASSET_KEY] == "cut"


def test_a_part_is_shown_by_its_figure_s_crop() -> None:
    page = [{"index": 1, "width": 1000, "height": 1400, "name": None}]
    records = {"nodes": [
        {"id": "d", "labels": ["DocNode", "Document"], "properties": {"meta_pages": page}},
        {"id": "grid", "labels": ["DocNode", "Table"],
         "properties": {"meta_page": 1, f"meta_{CROP_ASSET_KEY}": "figure-crop"}},
        {"id": "icon", "labels": ["DocNode", "Image"],
         "properties": {"meta_page": 1, "meta_part_of_figure": "grid"}},
        {"id": "note", "labels": ["DocNode", "TextChunk"], "properties": {"meta_page": 1}},
    ]}

    shown = kept_from(records, DocumentFormat.PDF)

    assert shown is not None
    kept, _ = shown

    assert kept.crops == {"grid": "figure-crop", "icon": "figure-crop"}

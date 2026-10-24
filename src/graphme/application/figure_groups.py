"""What the page says is drawn as one figure, applied to the crop and to the outline.

Territory is measured: whatever lies inside a picture is part of it. A drawing's labels
are written beside the part they point at -- over the gap they name, under the piece they
describe -- so measurement leaves them outside while they are what the drawing is saying.
Nothing in the shapes tells that apart from a heading that happens to sit near a
photograph; the page does, and the hierarchy pass is asked.

What it names becomes part of the picture: the crop is cut again around the picture and
its parts, and each part reads under the picture. The parts keep their own nodes and
their own text -- they are still the words on the page -- and only what a reader is shown
for the picture changes.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from graphme.application.unit_placement import UnitPlacer
from graphme.application.visual_crops import CONTAINER_TYPES, CROP_ASSET_KEY, crop_box
from graphme.domain.enums import NodeType
from graphme.domain.models import ExtractedDocument, GraphNode, HierarchyFigureGroup
from graphme.ports.storage import AssetStore

_log = logging.getLogger(__name__)


def widen_figures(
    extracted: ExtractedDocument,
    *,
    figures: Sequence[HierarchyFigureGroup],
    placers: Mapping[int, UnitPlacer],
    page_of: Mapping[str, int],
    crop: Any,
    asset_store: AssetStore,
) -> ExtractedDocument:
    """Cut each named figure's crop again, around the figure and the parts drawn with it.

    `crop` cuts a box out of the page the figure is on, the way the binding cut the first
    one; `placers` is how a box is found on that page, one per page index.
    """
    if not figures:
        return extracted
    nodes = {node.id: node for node in extracted.nodes}
    widened: dict[str, tuple[str, tuple[int, int, int, int]]] = {}
    part_of: dict[str, str] = {}
    for group in figures:
        figure = nodes.get(group.figure_id)
        page = page_of.get(group.figure_id)
        placer = placers.get(page) if page is not None else None
        if figure is None or placer is None:
            continue
        # The territory it already holds, not its own rectangle: whatever the binding
        # took in is part of the figure, and cutting again from the rectangle sliced a
        # photograph the figure owned in half.
        box = crop_box(figure) or placer.box(figure)
        if box is None:
            continue
        table = figure.type in CONTAINER_TYPES
        # A table is its grid: its frame is what its own pieces cover, and words outside
        # that frame are not in it, whatever the page grouped them with. A heading named
        # with a table stretched it up to the heading, and every summary line in between
        # was then taken as the table's -- the outline read them all under the table they
        # introduce. A picture's labels are the other way round: they stand outside it.
        frame = box
        if table:
            for member_id in group.member_ids:
                member = nodes.get(member_id)
                if (member is not None and member.type in CONTAINER_TYPES
                        and page_of.get(member_id) == page
                        and (piece := crop_box(member) or placer.box(member)) is not None):
                    frame = frame.union(piece)
        members: list[str] = []
        for member_id in group.member_ids:
            member = nodes.get(member_id)
            if member is None or page_of.get(member_id) != page:
                continue  # a part on another page is not drawn with this figure
            # The part's territory too: a photograph that took in the ones beside it
            # brings all of them, and adding only its own rectangle cut the rest in half.
            part = crop_box(member) or placer.box(member)
            if (table and member.type is NodeType.TEXT_CHUNK
                    and (part is None or not frame.contains(part))):
                continue  # outside the grid: the outline places it
            members.append(member_id)
            if part is not None:
                box = box.union(part)
        rounded = box.rounded()
        # Nothing moved: the page named parts that are not drawn where this figure is.
        if list(rounded) == (figure.metadata.get("crop_bbox") or []):
            continue
        try:
            asset_hash = asset_store.put(crop(page, rounded), suffix=".png")
        except (OSError, ValueError) as error:
            _log.warning("figure %s could not be cut again -- %s", group.figure_id, error)
            continue
        widened[group.figure_id] = (asset_hash, rounded)
        for member_id in members:
            part_of[member_id] = group.figure_id
        if table:
            # A table drawn with a band across it is measured as two tables with the
            # band's words standing between them. Once the page has said they are one
            # table, those words are its row -- the page named the tables and left the
            # band out, and it hung outside the table it is printed in. A picture is not
            # widened this way: a paragraph can stand between two photographs of one
            # figure, and a table's frame cannot hold one.
            for node in extracted.nodes:
                if (
                    node.type is NodeType.TEXT_CHUNK
                    and node.id not in part_of
                    and page_of.get(node.id) == page
                    and (where := placer.box(node)) is not None
                    and frame.contains(where)
                ):
                    part_of[node.id] = group.figure_id
    if not widened:
        return extracted
    return extracted.model_copy(
        update={
            "nodes": tuple(_reshaped(node, widened, part_of) for node in extracted.nodes)
        }
    )


def _reshaped(
    node: GraphNode,
    widened: Mapping[str, tuple[str, tuple[int, int, int, int]]],
    part_of: Mapping[str, str],
) -> GraphNode:
    """The figure with its new crop, or a part with the figure it was drawn with."""
    cut = widened.get(node.id)
    if cut is not None:
        asset_hash, box = cut
        return node.model_copy(
            update={
                "metadata": {
                    **node.metadata,
                    CROP_ASSET_KEY: asset_hash,
                    "crop_bbox": list(box),
                    # The first cut was measured; this one is what the page said.
                    "crop_widened_by": "figure_parts",
                }
            }
        )
    figure_id = part_of.get(node.id)
    if figure_id is None:
        return node
    # A part is shown with the figure it belongs to, not on its own beside it.
    return node.model_copy(update={"metadata": {**node.metadata, "part_of_figure": figure_id}})

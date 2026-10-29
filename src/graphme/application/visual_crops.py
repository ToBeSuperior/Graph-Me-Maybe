"""Give the graph's visual nodes their rendered crop, and put pictures where they sit.

Extraction knows a table's cell range and a picture's anchor but has never seen the
document drawn, so a node has no image of itself. The browser render supplies that: a
cell range is turned into pixels by lookup and the crop is stored against the node.

A picture whose cells fall inside a table is part of that table. Extraction parents it
by position on the sheet, which lands it beside whatever text happens to share its
neighbourhood; here it is re-owned by the table that actually encloses it.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from graphme.application.page_regions import (
    TOUCH_SLACK_PIXELS,
    PixelBox,
    SheetPixelGrid,
)
from graphme.application.unit_placement import UnitPlacer
from graphme.application.unit_regions import (
    PlacedUnit,
    RegionKind,
    build_unit_regions,
)
from graphme.domain.enums import DocumentFormat, EdgeType, NodeType
from graphme.domain.models import ExtractedDocument, GraphEdge, GraphNode
from graphme.ports.storage import AssetStore

VISUAL_TYPES = (NodeType.TABLE, NodeType.PIVOT_TABLE, NodeType.CHART, NodeType.IMAGE)
"""What is read as a picture: cropped, captioned, and able to hold other units. The one
definition -- three modules each kept their own copy of it."""
CONTAINER_TYPES = {NodeType.TABLE, NodeType.PIVOT_TABLE}
FIGURE_TYPES = {NodeType.IMAGE, NodeType.CHART}
"""What a page draws lines around: a diagram's frame and its arrows belong to the figure,
never to a table, whose own rules already say what its cells hold."""
ENCLOSED_TYPES = {NodeType.TEXT_CHUNK}
"""Text takes part in the merge so a table owns the text it encloses, the same way it owns
a picture or a nested table. It gets no crop of its own: enclosed text is part of the table,
and text that no table encloses is read from the source, not from a picture of it."""
PLACED_TYPES = {*VISUAL_TYPES, *ENCLOSED_TYPES}


BORDER_ENCLOSURE = 0.9
"""How much of every other unit a wordless table has to hold before it is read as the
border drawn around them rather than a table of its own."""

BACKGROUND_COVERAGE = 0.95
"""How much of the page a thing has to cover to be what the page is drawn on rather than
something on it. A backdrop overlaps everything drawn over it, so under the rule that a
figure owns what it covers, one picture would otherwise swallow the whole page.

On paper a band laid edge to edge across the page -- its whole width or its whole height
-- is the same thing over part of it: a cover printed its title on a coloured band, and the
band took the title. It also runs off the page's edge the other way, as a printed band does:
a screenshot the width of the page standing in the body is content, and keeps what is written
on it. A sheet has no paper edge: it is as wide as what is drawn on it, so its widest picture
always reaches both sides."""


def _bleeds(a0: float, a1: float, b0: float, b1: float, across: float, along: float) -> bool:
    """Edge to edge across the page (`a`), and off one of its edges the other way (`b`)."""
    edge = (1 - BACKGROUND_COVERAGE) * along
    return a1 - a0 >= BACKGROUND_COVERAGE * across and (b0 <= edge or b1 >= along - edge)


def _borders(placed: Sequence[_Placed]) -> set[str]:
    """Tables that are really a border drawn around the page.

    Reports are routinely drawn inside a frame, and a frame parses as a table: one with no
    words of its own, enclosing everything on the page. Under the rule that a table owns
    what it encloses, that frame swallowed the whole document into one node.

    Neither test is enough alone. A table of photographs has no words either, and a real
    table can be the only thing on a page. A frame is both: it says nothing and it holds
    everything -- and what it holds is a report, words or tables, not only pictures. A grid
    of photographs with no captions in its cells held its photographs and was set aside as
    a border, so they stood loose beside it.
    """
    if len(placed) < 2:
        return set()
    borders: set[str] = set()
    for item in placed:
        if item.node.type not in CONTAINER_TYPES or any(
            character.isalnum() for character in (item.node.text or "")
        ):
            continue
        others = [other for other in placed if other is not item]
        if all(item.box.covered_fraction(o.box) >= BORDER_ENCLOSURE for o in others) and any(
            o.node.type not in FIGURE_TYPES for o in others
        ):
            borders.add(item.node.id)
    return borders


JOIN_REACH_PIXELS = 8.0
"""How far from a connector's rectangle a shape may stand and still be the one it names."""


def _joined_ids(
    node: GraphNode,
    numbered: Mapping[int, Sequence[str]],
    boxes: Mapping[str, PixelBox],
) -> tuple[str, ...]:
    """Node ids for the shapes a connector states it joins, by their shape numbers.

    Numbers repeat on a slide -- a pasted group keeps its children's numbers -- so where two
    shapes answer to the number, the one the connector actually reaches is the one it names.
    An arrow naming a number shared with a strip of logos across the slide joined the logos
    into its diagram.
    """
    stated = node.metadata.get("joins")
    own = boxes.get(node.id)
    if not isinstance(stated, list) or own is None:
        return ()
    joined: list[str] = []
    for number in {value for value in stated if isinstance(value, int)}:
        named = numbered.get(number, ())
        if len(named) > 1:
            named = [
                other for other in named
                if (box := boxes.get(other)) is not None
                and own.touches(box, slack=JOIN_REACH_PIXELS)
            ]
        joined.extend(named)
    return tuple(joined)


def _has_area(box: PixelBox) -> bool:
    left, top, right, bottom = box.rounded()
    return right > left and bottom > top


LINE_PIXELS = 4.0
"""How thin a drawing is before it is a line: a stroke, not a picture with an area."""


def _region_kind(node: GraphNode, box: PixelBox) -> RegionKind:
    if node.type in CONTAINER_TYPES:
        return "table"
    if node.type in ENCLOSED_TYPES:
        return "text"
    # A line joins nothing by lying next to it. A divider under a title runs the whole width
    # and touches everything above and below it; merged on touch it chained a strip of logos,
    # a callout and a table into one region. Like an arrow, it is taken in by what encloses
    # it and takes nothing in itself.
    thin = min(box.x1 - box.x0, box.y1 - box.y0) <= LINE_PIXELS
    return "connector" if node.metadata.get("joins") or thin else "drawing"

CROP_ASSET_KEY = "crop_asset_hash"


def crop_box(node: GraphNode) -> PixelBox | None:
    """The box the binding cut this node's crop to, when it cut one."""
    stored = node.metadata.get("crop_bbox")
    if not isinstance(stored, list) or len(stored) != 4:
        return None
    if not all(isinstance(value, int | float) and not isinstance(value, bool) for value in stored):
        return None
    x0, y0, x1, y1 = (float(value) for value in stored)  # type: ignore[arg-type]
    return PixelBox(x0, y0, x1, y1)


def read_node_crop(node: GraphNode, *, asset_store: AssetStore) -> bytes | None:
    """The crop this module stored for a node, or None when it stored none.

    It lives beside the write so the two cannot drift. Consumers used to cut their own
    crop out of the page image from a box they worked out themselves, and two of them
    worked out different boxes for the same merged picture -- the hierarchy pass then
    showed the model one node's id beside another node's photograph.
    """
    asset_hash = node.metadata.get(CROP_ASSET_KEY)
    if not isinstance(asset_hash, str):
        return None
    return read_crop(asset_hash, asset_store)


def read_crop(asset_hash: str, asset_store: AssetStore) -> bytes | None:
    """A stored crop by its hash, or None when it was never written."""
    try:
        crop: bytes = asset_store.read(asset_hash, ".png")
    except (OSError, KeyError):
        return None  # a crop that was never written is not worth failing a read for
    return crop


CropCallable = Callable[[tuple[int, int, int, int]], bytes]


class VisualBinder(Protocol):
    """Attaches rendered crops and table ownership to a document's visual nodes."""

    def bind(
        self, *, extracted: ExtractedDocument, source: Path
    ) -> ExtractedDocument: ...

    def widen(
        self, *, extracted: ExtractedDocument, source: Path, figures: Sequence[Any]
    ) -> ExtractedDocument: ...

    def drawn(self, *, extracted: ExtractedDocument, source: Path) -> ExtractedDocument: ...


class RenderedSheet(Protocol):
    # Read-only, because a rendered page is frozen once drawn.
    @property
    def label(self) -> str | None: ...

    @property
    def png(self) -> bytes: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    def grid(self) -> SheetPixelGrid | None: ...


@dataclass(frozen=True, slots=True)
class _Placed:
    node: GraphNode
    box: PixelBox


def bind_page_visuals(
    extracted: ExtractedDocument,
    *,
    page: RenderedSheet,
    members: Sequence[GraphNode],
    placer: UnitPlacer,
    asset_store: AssetStore,
    crop: CropCallable,
    ink: Sequence[PixelBox] = (),
) -> ExtractedDocument:
    """Attach crops to one page's visual nodes and re-own what a table encloses.

    The rules do not change with the format: whatever overlaps or touches is one region,
    a table names the region it is part of, and everything the region swallowed is owned
    by that name rather than cropped again.

    `placer` is where a box comes from -- cells for a worksheet, the shape's own rectangle
    for a slide -- and is the only part that differs.
    """
    page_area = max(1, page.width * page.height)
    placed: list[_Placed] = []
    background: set[str] = set()
    # Anything the page does not show has no crop to take and takes part in nothing.
    shown = [
        (node, box)
        for node in members
        if node.type in PLACED_TYPES and (box := placer.box(node)) is not None
    ]
    if ink:
        # A table's grid is drawn as vector paths too. Those rules are the table's, and a
        # picture in a cell that took them in grew across the whole grid and ended up
        # owning the table it sits in.
        grids = [box for node, box in shown if node.type in CONTAINER_TYPES]
        ink = [
            rect for rect in ink
            if not any(grid.contains(rect, slack=TOUCH_SLACK_PIXELS) for grid in grids)
        ]
    paper = extracted.document.format is not DocumentFormat.XLSX
    for node, box in shown:
        covered = box.area / page_area
        band = paper and (_bleeds(box.x0, box.x1, box.y0, box.y1, page.width, page.height)
                          or _bleeds(box.y0, box.y1, box.x0, box.x1, page.height, page.width))
        # A backdrop is what something else is printed on; a picture filling the page with
        # nothing over it -- a scan pasted onto a slide -- is what the page says. A scan read
        # for its words is the page they are printed on, whatever its margins.
        printed_on = any(other is not node and box.covered_fraction(over) > 0
                         for other, over in shown)
        if node.type is NodeType.IMAGE and (
            ((covered >= BACKGROUND_COVERAGE or band) and printed_on)
            or node.metadata.get("background")
        ):
            background.add(node.id)
            continue
        if node.type in FIGURE_TYPES and ink:
            # A figure is what was drawn around it too. Taking the frame and the arrows in
            # here rather than at the crop means the figure owns what they enclose, the
            # same way a picture owns what its own rectangle covers.
            box = grown_by_ink(box, ink, slack=TOUCH_SLACK_PIXELS)
        placed.append(_Placed(node, box))
    borders = _borders(placed)
    background |= borders
    placed = [item for item in placed if item.node.id not in borders]
    if not placed:
        return extracted

    # One merge, shared with the extraction path: whatever overlaps is one region,
    # the table (or top-left picture) names it, and everything else it swallowed is
    # owned by that name rather than cropped again.
    boxes: dict[str, PixelBox] = {item.node.id: item.box for item in placed}
    numbered: dict[int, list[str]] = {}
    for item in placed:
        number = item.node.metadata.get("shape_id")
        if isinstance(number, int):
            numbered.setdefault(number, []).append(item.node.id)
    regions = build_unit_regions(
        tuple(
            PlacedUnit(
                id=item.node.id,
                kind=_region_kind(item.node, item.box),
                box=item.box,
                joins=_joined_ids(item.node, numbered, boxes),
            )
            for item in placed
        )
    )
    owner: dict[str, str] = {}
    counts: dict[str, int] = {}
    # Every piece keeps a crop of itself, so a single photograph can still be retrieved;
    # the region's own box goes to the unit that names it.
    for region in regions:
        boxes[region.lead_id] = region.box
        for member_id in region.member_ids:
            if member_id != region.lead_id:
                owner[member_id] = region.lead_id
        if region.drawing_count > 1 or (
            region.kind == "drawing" and region.drawing_count
        ):
            counts[region.lead_id] = region.drawing_count

    # Text takes part in the merge so a table can own it, but it is never cropped: the
    # words are read from the source, and a picture of them says nothing the text does not.
    visual = {item.node.id for item in placed if item.node.type in VISUAL_TYPES}
    crops = {
        node_id: (asset_store.put(crop(box.rounded()), suffix=".png"), box.rounded())
        for node_id, box in boxes.items()
        # A rule drawn across the page has no height to speak of. It still sits somewhere
        # and takes part in the merge, but a box that rounds away has no picture in it.
        if node_id in visual and _has_area(box)
    }

    nodes = tuple(
        node.model_copy(
            update={
                "metadata": {
                    **node.metadata,
                    CROP_ASSET_KEY: crops[node.id][0],
                    "crop_bbox": list(crops[node.id][1]),
                    "crop_page_index": getattr(page, "index", None),
                    **(
                        {"picture_count": counts[node.id]}
                        if node.id in counts
                        else {}
                    ),
                }
            }
        )
        if node.id in crops
        # The backdrop stays in the graph -- the file says it is there -- and is marked
        # so nothing downstream reads it as a thing on the page.
        else node.model_copy(update={"metadata": {**node.metadata, "background": True}})
        if node.id in background
        else node
        for node in extracted.nodes
    )
    # The canonical graph allows one incoming CONTAINS per node, so the old owner edge
    # is replaced rather than joined.
    edges = [
        edge
        for edge in extracted.edges
        if not (edge.type is EdgeType.CONTAINS and edge.target_id in owner)
    ]
    edges.extend(
        GraphEdge(
            id=f"contains:{parent_id}->{child_id}",
            document_id=extracted.document.id,
            source_id=parent_id,
            target_id=child_id,
            type=EdgeType.CONTAINS,
            metadata={"basis": "enclosed_by_rendered_range"},
        )
        for child_id, parent_id in owner.items()
    )

    return extracted.model_copy(
        update={"nodes": nodes, "edges": tuple(edges)}
    )


def grown_by_ink(
    box: PixelBox, ink: Sequence[PixelBox], *, slack: float
) -> PixelBox:
    """A figure's box, widened to take in what was drawn around it.

    A PDF states its frames, arrows and callouts as vector paths. They are not pictures,
    they never become nodes, and a crop cut to the pictures alone shows the parts of a
    diagram without the box that groups them or the arrow that reads between them.

    Taking in reaches on: a frame the figure touches brings whatever that frame touches.
    """
    grown = box
    left = list(ink)
    moved = True
    while moved:
        moved = False
        still: list[PixelBox] = []
        for rect in left:
            if grown.touches(rect, slack=slack):
                grown = grown.union(rect)
                moved = True
            else:
                still.append(rect)
        left = still
    return grown



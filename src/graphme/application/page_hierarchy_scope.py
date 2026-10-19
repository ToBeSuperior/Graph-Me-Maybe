"""Assemble one rendered page's hierarchy scope from source extraction and its rendering.

What the extraction READ off the file is sent: a unit's text, its cell range turned into
pixels, and whether it is a table, a picture or text. What the extraction JUDGED is not.
"heading" and "caption" are guesses a marker or a short line provoked, and the parent
links are a guess about the outline -- measured against a page the model reads directly,
those links agreed only half the time, and offering them as a default moved the model's
answer by 38 points. So the outline is derived from the rendering, and the deterministic
pass supplies only what it can point at in the file.

A table is sent as its own heading row rather than its contents: the hierarchy question
is where the table sits, and its data is already captured elsewhere.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from graphme.application.page_units import held_by_a_visual
from graphme.application.reading_order import in_columns
from graphme.application.unit_placement import CellRangePlacer, DrawingLike, UnitPlacer
from graphme.application.xlsx_crop_regions import PixelBox, SheetPixelGrid
from graphme.application.xlsx_visual_binding import crop_box
from graphme.domain.enums import NodeType
from graphme.domain.models import ExtractedDocument, GraphNode, HierarchyUnit


@dataclass(frozen=True, slots=True)
class HierarchyScopeUnits:
    """One rendered page's units, ready for a hierarchy confirmation pass."""

    scope: str
    page_index: int
    scope_root_id: str
    units: tuple[HierarchyUnit, ...]


MAX_LABEL_CHARS = 200

_NOT_ON_THE_PAGE = {NodeType.DOCUMENT}
"""The document itself, which every page of running text reads under."""


def _leading_spaces(node: GraphNode) -> int:
    """How many spaces the first line of a unit begins with.

    A report indents with the paragraph or with spaces inside the line, and uses both on
    one page. The label is sent trimmed, so this is the only place the second one shows.
    """
    stated = node.metadata.get("leading_spaces")
    if isinstance(stated, int) and not isinstance(stated, bool):
        return stated
    first = (node.text or "").splitlines()[:1]
    if not first:
        return 0
    return len(first[0]) - len(first[0].lstrip())


_Entry = tuple[str, NodeType, tuple[int, int, int, int], str]
"""One unit as the page shows it: its id, the type being sent, its box and its label."""


def table_label(text: str | None, cell_range: str) -> str:
    """A table's own heading row, which is how the hierarchy pass should see it. The marker is
    in the prompts' language, English, whatever language the document is in."""
    if not text:
        return f"[table] {cell_range}"
    cells = [cell.strip() for cell in text.splitlines()[0].split("|")]
    unique = [cell for index, cell in enumerate(cells) if cell and cell not in cells[:index]]
    return f"[table] {' / '.join(unique) or cell_range} ({cell_range})"


def unit_label(node: GraphNode) -> str:
    """How a unit is named wherever one is shown -- the payload and the demo alike."""
    if node.type is NodeType.TABLE and node.locator.cell_range:
        return table_label(node.text, node.locator.cell_range)
    source = (node.text or node.label).strip()
    first = source.splitlines()[0] if source else node.label
    return first[:MAX_LABEL_CHARS] or node.label


def build_sheet_scope(
    *,
    extracted: ExtractedDocument,
    sheet_name: str,
    scope: str,
    page_index: int,
    grid: SheetPixelGrid,
    extents: Mapping[int, DrawingLike] | None = None,
) -> HierarchyScopeUnits | None:
    """Return the scope for one rendered worksheet, or None when nothing was placed.

    A worksheet is asked like any other page (`build_placed_scope`): what a visual holds is
    part of it, not a unit beside it. Only the placing is the sheet's own. Text and tables are
    where their cells are -- a sheet is a grid without end, and nothing else bounds them --
    and a picture or chart is its DrawingML extent (`extents`, by `xml_index`), the box its
    crop was cut by, since a resized picture keeps a stale anchor cell.
    """
    members = [node for node in extracted.nodes if node.locator.sheet == sheet_name]
    worksheet = next((node for node in members if node.type is NodeType.WORKSHEET), None)
    if worksheet is None:
        return None
    return build_placed_scope(
        extracted=extracted,
        members=members,
        placer=CellRangePlacer(grid, extents=extents),
        root=worksheet,
        scope=scope,
        page_index=page_index,
    )


def build_placed_scope(
    *,
    extracted: ExtractedDocument,
    members: Sequence[GraphNode],
    placer: UnitPlacer,
    root: GraphNode,
    scope: str,
    page_index: int,
    indented: bool = False,
    page_width: float | None = None,
) -> HierarchyScopeUnits | None:
    """Return the scope for one page whose units carry their own position.

    A slide states a shape's rectangle and a page of running text is measured where the
    words were drawn; either way the box is already known and the rules on top are the
    same. A figure owns the text, table, chart and shapes drawn within its territory, and
    what it owns is part of it rather than a unit beside it -- the binding phase decided
    that and wrote it down.

    `root` is what the page's units read under: the slide for a deck, the document itself
    for running text, where a page is only how much is looked at in one go.
    `indented` is for those pages, where the left edge of a line means nesting.
    `page_width` is for those pages too: running text can be set in columns, and a unit is
    read where its column puts it, not where its top edge falls.
    """
    absorbed = held_by_a_visual(extracted)
    entries: list[_Entry] = []
    spaces: dict[str, int] = {}
    for node in members:
        if node.id == root.id or node.type in _NOT_ON_THE_PAGE or node.id in absorbed:
            continue
        # The backdrop is what the page is drawn on, not a thing on it.
        if node.metadata.get("background"):
            continue
        box = placer.box(node)
        if box is None:  # anything outside the rendering cannot be judged visually
            continue
        spaces[node.id] = _leading_spaces(node)
        entries.append(_entry(node, box))
    if not entries:
        return None
    return HierarchyScopeUnits(
        scope=scope,
        page_index=page_index,
        scope_root_id=root.id,
        units=_units(
            entries,
            scope=scope,
            spaces=spaces if indented else None,
            page_width=page_width,
        ),
    )


def _entry(node: GraphNode, box: PixelBox) -> _Entry:
    """One unit as the page shows it.

    A picture is labelled with how much its region took in, because "그림 5" says nothing
    about which picture it is; everything else is named by its own first line.
    """
    if node.type is not NodeType.IMAGE:
        return (node.id, node.type, (crop_box(node) or box).rounded(),
                unit_label(node))
    count = node.metadata.get("picture_count")
    count = count if isinstance(count, int) and count else 1
    label = f"[picture] {node.label}"
    if count > 1:
        label += f" and {count - 1} more"
    return (node.id, NodeType.IMAGE, (crop_box(node) or box).rounded(), label)


def _units(
    entries: list[_Entry],
    *,
    scope: str,
    spaces: Mapping[str, int] | None = None,
    page_width: float | None = None,
) -> tuple[HierarchyUnit, ...]:
    """The units in reading order, which is the order the page puts them in.

    The model is told this list is the reading order, so it is: on a page of running text
    a column is read to its foot before the next begins. Top-to-bottom zigzags down a
    two-column page and hands over a section before the heading it belongs under.
    """
    if page_width:
        placed = in_columns(entries, lambda entry: entry[2], width=page_width)
        entries[:] = [entry for entry, _ in placed]
        # How far a unit is set in is measured from its own column's edge.
        column_left = {entry[0]: left for entry, left in placed}
    else:
        entries.sort(key=lambda entry: (entry[2][1], entry[2][0]))
        column_left = {}
    levels = (
        _indent_levels(
            [
                (
                    entry[0],
                    int(entry[2][0] - column_left.get(entry[0], 0.0)),
                    spaces.get(entry[0], 0),
                )
                for entry in entries
            ]
        )
        if spaces is not None
        else {}
    )
    return tuple(
        HierarchyUnit(
            id=unit_id,
            label=label,
            scope=scope,
            order=order,
            node_type=node_type,
            indent=levels.get(unit_id),
        )
        for order, (unit_id, node_type, _box, label) in enumerate(entries, start=1)
    )


INDENT_STEP_PIXELS = 12.0
"""How far apart two left edges have to be to be different indents. A tab in a report is
wider than this; the wobble within one level is not."""


def _indent_levels(placed: Sequence[tuple[str, int, int]]) -> dict[str, int]:
    """Turn where each unit's words start into levels, 0 being the leftmost.

    A document says indentation two ways and uses both in the same page: the paragraph is
    set in from the margin, or the line begins with spaces. Reading only the first left
    every line of one report at the same level, because its nesting was written with
    spaces inside the text.

    The distance itself says nothing across documents -- a page sets its own steps -- so
    what goes out is which step a unit stands on.
    """
    step = max(1, int(INDENT_STEP_PIXELS))
    start = {unit_id: (left // step, space) for unit_id, left, space in placed}
    level_of = {where: level for level, where in enumerate(sorted(set(start.values())))}
    return {unit_id: level_of[where] for unit_id, where in start.items()}

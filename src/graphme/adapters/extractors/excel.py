from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.reader.excel import ExcelReader
from openpyxl.utils.cell import range_boundaries
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.worksheet import Worksheet

from graphme.adapters.extractors.xlsx_drawings import (
    DrawingComponent,
    DrawingManifest,
    DrawingObject,
    anchor_cell_range,
    group_drawing_components,
    parse_xlsx_drawings,
)
from graphme.adapters.extractors.xlsx_visual_tables import (
    GridRectangle,
    VisualTableCandidate,
    detect_data_blocks,
    detect_visual_tables,
    is_formula,
    written_cells,
)
from graphme.domain.enums import (
    DocumentFormat,
    EdgeType,
    EvidenceKind,
    NodeType,
    Severity,
)
from graphme.domain.ids import (
    canonical_locator,
    sha256_file,
    stable_id,
)
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    JsonValue,
    SourceLocator,
    WarningRecord,
)
from graphme.ports.storage import AssetStore

# A direct A1-style reference: optional quoted or bare sheet name + $-absolute
# cell or range. A bare name starts with a letter of any script: Excel quotes a sheet
# name only for spaces and punctuation, not for being Korean. Deliberately excludes
# function calls (INDIRECT/OFFSET) and external-workbook references ([...]) — those are
# skipped with a warning.
_REF_RE = re.compile(
    r"(?:'(?P<qsheet>[^']+)'|(?P<sheet>[^\W\d][\w.]*))!"
    r"\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)"
    r"(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?"
)
_DYNAMIC_FUNCS = ("INDIRECT", "OFFSET")


def _ref_parts(match: re.Match[str], dollar: str = "") -> tuple[str, str]:
    """A `_REF_RE` match as (sheet, "A1" or "A1:B2"), each cell written with `dollar`."""
    sheet = match.group("qsheet") or match.group("sheet")
    cell_ref = f"{dollar}{match.group('c1')}{dollar}{match.group('r1')}"
    if match.group("c2"):
        cell_ref += f":{dollar}{match.group('c2')}{dollar}{match.group('r2')}"
    return sheet, cell_ref

def _bounds(cell_ref: str) -> tuple[int, int, int, int]:
    """`cell_ref` ("A1" or "A1:B2") as `iter_rows`' min_row, max_row, min_col, max_col."""
    min_col, min_row, max_col, max_row = range_boundaries(cell_ref)
    return min_row, max_row, min_col, max_col


DATA_BLOCK_ROWS = 100
"""How many rows running, each holding two values or more, make a table with no border.

A table is found by the rule drawn round it, and a recorded log has none: every value of
it became a unit of its own, hundreds of thousands for a long one. Below this a run of
filled rows is as likely a form or a list as a table, and stays as its cells."""

LONG_TABLE_ROWS = 500
TABLE_HEAD_ROWS = 20
TABLE_FOOT_ROWS = 20
"""A table longer than `LONG_TABLE_ROWS` keeps only its head, which carries the headings,
and its foot, where a log is summed up; what stands between is said, not kept. A checklist
or an item list of a few hundred rows is read whole -- every row of it is content."""



class _Reader(ExcelReader):  # type: ignore[misc]
    def read_custom(self) -> None:
        """Custom document properties are never read here, and openpyxl fails the whole
        file over one it cannot type (a property with no name)."""


def open_workbook(source: Path, *, read_only: bool = False, data_only: bool = False) -> Any:
    """`openpyxl.load_workbook`, without the custom document properties."""
    reader = _Reader(source, read_only=read_only, data_only=data_only)
    reader.read()
    return reader.wb


@dataclass(frozen=True)
class _TableRange(GridRectangle):
    """A table's (or a pivot's cache source's) rectangle on a sheet, by node."""

    node_id: str
    sheet: str


@dataclass(frozen=True)
class _LayoutUnit:
    row: int
    col: int
    item_id: str
    kind: str
    text: str = ""
    range_ref: str | None = None
    parent_id: str | None = None

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        if self.range_ref is None:
            return self.col, self.row, self.col, self.row
        min_col, min_row, max_col, max_row = range_boundaries(self.range_ref)
        return int(min_col), int(min_row), int(max_col), int(max_row)


def _drawing_member_data(component: DrawingComponent) -> list[dict[str, JsonValue]]:
    return [
        {
            "object_id": member.object_id,
            "kind": member.kind,
            "z_index": member.z_index,
            "group_path": list(member.group_path),
            "nv_id": member.nv_id,
            "name": member.name,
            "anchor_kind": member.anchor_kind,
            "anchor_from": cast(
                dict[str, JsonValue], member.anchor_from.as_dict()
            ) if member.anchor_from else None,
            "anchor_to": cast(
                dict[str, JsonValue], member.anchor_to.as_dict()
            ) if member.anchor_to else None,
            "extent": cast(
                dict[str, JsonValue], member.extent.as_dict()
            ) if member.extent else None,
            "transform": member.transform,
            "properties": member.properties,
            "text_runs": list(member.text_runs),
            "relationship_id": member.media_relationship_id,
            "media_target": member.media_target,
            "media_hash": member.media_hash,
            "raw_xml_hash": member.raw_xml_hash,
        }
        for member in component.members
    ]


class ExcelExtractor:
    """Extracts typed hierarchy nodes and evidence from an .xlsx workbook.

    MVP scope (this cycle): document/workbook/worksheet/table nodes, CONTAINS
    hierarchy edges, and source evidence. Formula dependencies, SAME_SHEET,
    charts, pivots, and deterministic rendering are added by later cycles.
    Recognizes only explicit Excel Table objects; no contiguous-range guessing.
    """

    format = DocumentFormat.XLSX

    def __init__(
        self,
        *,
        asset_store: AssetStore | None = None,
    ) -> None:
        self._asset_store = asset_store

    def extract(self, source: Path) -> ExtractedDocument:
        content_hash = sha256_file(source)
        wb = open_workbook(source, data_only=False)
        cached_wb = open_workbook(source, data_only=True)
        drawing_manifest = parse_xlsx_drawings(source, workbook_hash=content_hash)

        def node_id(kind: str, coords: dict[str, str]) -> str:
            return stable_id(content_hash, canonical_locator(kind, coords))

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        evidence: list[EvidenceRecord] = []
        warnings: list[WarningRecord] = []
        document_id = node_id("document", {"file": source.name})
        for warning_index, message in enumerate(drawing_manifest.warnings):
            warnings.append(
                WarningRecord(
                    id=stable_id(content_hash, f"warn:drawing_manifest:{warning_index}"),
                    document_id=document_id,
                    code="drawing_manifest_warning",
                    message=message,
                    severity=Severity.WARNING,
                    locator=SourceLocator(file_name=source.name, label=source.name),
                )
            )

        def add_evidence(
            nid: str, label: str, kind: EvidenceKind, text: str, loc: SourceLocator
        ) -> None:
            evidence.append(
                EvidenceRecord(
                    id=stable_id(content_hash, f"evidence:{label}"),
                    document_id=document_id,
                    node_id=nid,
                    kind=kind,
                    locator=loc,
                    text=text,
                )
            )

        doc_locator = SourceLocator(file_name=source.name, label=source.name)
        nodes.append(
            GraphNode(
                id=document_id,
                document_id=document_id,
                type=NodeType.DOCUMENT,
                label=source.name,
                locator=doc_locator,
                content_hash=content_hash,
            )
        )
        add_evidence(
            document_id,
            "document",
            EvidenceKind.SOURCE_METADATA,
            f"workbook {source.name}",
            doc_locator,
        )

        workbook_id = node_id("workbook", {"file": source.name})
        wb_locator = SourceLocator(file_name=source.name, label="workbook")
        nodes.append(
            GraphNode(
                id=workbook_id,
                document_id=document_id,
                type=NodeType.WORKBOOK,
                label="workbook",
                locator=wb_locator,
            )
        )
        add_evidence(
            workbook_id,
            "workbook",
            EvidenceKind.SOURCE_METADATA,
            f"workbook with sheets {wb.sheetnames}",
            wb_locator,
        )
        edges.append(self._contains(content_hash, document_id, document_id, workbook_id))

        table_ranges: list[_TableRange] = []
        sheet_ids: dict[str, str] = {}
        pivot_sources: list[_TableRange] = []
        pivot_ranges: list[_TableRange] = []
        chart_sources: list[tuple[str, list[str]]] = []
        sheet_cells = {sheet.title: written_cells(sheet) for sheet in wb.worksheets}
        # Grouped once for the workbook; each sheet takes its own, in the same order.
        sheet_pictures: dict[str, list[DrawingComponent]] = {}
        if self._asset_store is not None:
            for component in group_drawing_components(drawing_manifest):
                if component.pictures:
                    sheet_pictures.setdefault(component.worksheet, []).append(component)
        for sheet in wb.worksheets:
            self._extract_sheet(
                sheet=sheet,
                cells=sheet_cells[sheet.title],
                value_sheet=cached_wb[sheet.title],
                source=source,
                content_hash=content_hash,
                document_id=document_id,
                workbook_id=workbook_id,
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                warnings=warnings,
                node_id=node_id,
                add_evidence=add_evidence,
                table_ranges=table_ranges,
                sheet_ids=sheet_ids,
                pivot_sources=pivot_sources,
                pivot_ranges=pivot_ranges,
                chart_sources=chart_sources,
                drawing_manifest=drawing_manifest,
                pictures=sheet_pictures.get(sheet.title, []),
            )

        self._link_pivots(
            content_hash=content_hash,
            document_id=document_id,
            edges=edges,
            table_ranges=table_ranges,
            pivot_sources=pivot_sources,
        )
        for chart_id, refs in chart_sources:
            for source_range in self._resolve_refs(" ".join(refs), [*table_ranges, *pivot_ranges]):
                edges.append(
                    GraphEdge(
                        id=stable_id(
                            content_hash,
                            f"references:{chart_id}->{source_range.node_id}",
                        ),
                        document_id=document_id,
                        source_id=chart_id,
                        target_id=source_range.node_id,
                        type=EdgeType.REFERENCES,
                    )
                )

        defined_ranges = self._parse_defined_names(wb)
        self._link_formulas(
            wb=wb,
            source=source,
            content_hash=content_hash,
            document_id=document_id,
            edges=edges,
            warnings=warnings,
            table_ranges=table_ranges,
            sheet_ids=sheet_ids,
            defined_ranges=defined_ranges,
            sheet_cells=sheet_cells,
        )

        extracted = ExtractedDocument(
            document=DocumentRecord(
                id=document_id,
                content_hash=content_hash,
                file_name=source.name,
                format=DocumentFormat.XLSX,
                source_path=source,
                size_bytes=source.stat().st_size,
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
            evidence=tuple(evidence),
            warnings=tuple(warnings),
        )
        return extracted.validate_canonical_semantic_graph()

    def _contains(
        self, content_hash: str, document_id: str, source_id: str, target_id: str
    ) -> GraphEdge:
        return GraphEdge(
            id=stable_id(content_hash, f"contains:{source_id}->{target_id}"),
            document_id=document_id,
            source_id=source_id,
            target_id=target_id,
            type=EdgeType.CONTAINS,
        )

    def _extract_sheet(
        self,
        *,
        sheet: Worksheet,
        cells: list[Cell],
        value_sheet: Worksheet,
        source: Path,
        content_hash: str,
        document_id: str,
        workbook_id: str,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        evidence: list[EvidenceRecord],
        warnings: list[WarningRecord],
        node_id: Callable[[str, dict[str, str]], str],
        add_evidence: Callable[[str, str, EvidenceKind, str, SourceLocator], None],
        table_ranges: list[_TableRange],
        sheet_ids: dict[str, str],
        pivot_sources: list[_TableRange],
        pivot_ranges: list[_TableRange],
        chart_sources: list[tuple[str, list[str]]],
        drawing_manifest: DrawingManifest,
        pictures: list[DrawingComponent],
    ) -> None:
        first_node = len(nodes)
        hidden = sheet.sheet_state != "visible"
        sheet_id = node_id("worksheet", {"sheet": sheet.title})
        sheet_ids[sheet.title] = sheet_id
        sheet_locator = SourceLocator(file_name=source.name, label=sheet.title, sheet=sheet.title)
        nodes.append(
            GraphNode(
                id=sheet_id,
                document_id=document_id,
                type=NodeType.WORKSHEET,
                label=sheet.title,
                locator=sheet_locator,
                metadata={"hidden": hidden},
            )
        )
        add_evidence(
            sheet_id,
            f"worksheet:{sheet.title}",
            EvidenceKind.SOURCE_METADATA,
            f"worksheet {sheet.title} (hidden={hidden})",
            sheet_locator,
        )
        edges.append(self._contains(content_hash, document_id, workbook_id, sheet_id))

        table_ids: list[str] = []
        ordered_items: list[_LayoutUnit] = []
        sheet_table_ranges: list[_TableRange] = []

        def add_table(table_id: str, label: str, ref: str, metadata: dict[str, Any]) -> None:
            table_range = self._emit_table(
                sheet=sheet,
                value_sheet=value_sheet,
                source=source,
                document_id=document_id,
                nodes=nodes,
                add_evidence=add_evidence,
                table_id=table_id,
                label=label,
                ref=ref,
                metadata=metadata,
            )
            table_ids.append(table_id)
            table_ranges.append(table_range)
            sheet_table_ranges.append(table_range)
            ordered_items.append(
                _LayoutUnit(
                    table_range.min_row, table_range.min_col, table_id, "table", range_ref=ref
                )
            )

        for name in sheet.tables:
            add_table(
                node_id("table", {"sheet": sheet.title, "name": name}),
                name,
                sheet.tables[name].ref,
                {"table_kind": "formal", "formal_name": name},
            )

        for candidate in detect_visual_tables(sheet, cells):
            # Slot ownership is resolved from parsed anchor geometry during image
            # extraction, never from component enumeration order.
            add_table(
                node_id("visual_table", {"sheet": sheet.title, "range": candidate.ref}),
                f"Visual table {candidate.ref}",
                candidate.ref,
                self._visual_table_metadata(candidate),
            )

        for ref in detect_data_blocks(cells, sheet_table_ranges, min_rows=DATA_BLOCK_ROWS):
            add_table(
                node_id("data_table", {"sheet": sheet.title, "range": ref}),
                f"Data table {ref}",
                ref,
                {"table_kind": "data"},
            )

        text_items = self._extract_native_text(
            sheet=sheet,
            cells=cells,
            value_sheet=value_sheet,
            source=source,
            content_hash=content_hash,
            document_id=document_id,
            nodes=nodes,
            evidence=evidence,
            node_id=node_id,
            table_ranges=sheet_table_ranges,
        )
        ordered_items.extend(text_items)
        ordered_items.extend(
            self._extract_images(
                sheet=sheet,
                source=source,
                content_hash=content_hash,
                document_id=document_id,
                nodes=nodes,
                evidence=evidence,
                node_id=node_id,
                components=pictures,
                table_ranges=sheet_table_ranges,
                warnings=warnings,
            )
        )
        self._emit_reading_hierarchy(
            content_hash=content_hash,
            document_id=document_id,
            sheet_id=sheet_id,
            ordered_items=ordered_items,
            nodes=nodes,
            first_node=first_node,
            edges=edges,
        )

        # One canonical (undirected) SAME_SHEET edge per unordered table pair.
        for i in range(len(table_ids)):
            for j in range(i + 1, len(table_ids)):
                source_id, target_id = sorted((table_ids[i], table_ids[j]))
                edges.append(
                    GraphEdge(
                        id=stable_id(content_hash, f"same_sheet:{source_id}->{target_id}"),
                        document_id=document_id,
                        source_id=source_id,
                        target_id=target_id,
                        type=EdgeType.SAME_SHEET,
                    )
                )

        self._extract_pivots(
            sheet=sheet,
            value_sheet=value_sheet,
            source=source,
            content_hash=content_hash,
            document_id=document_id,
            sheet_id=sheet_id,
            nodes=nodes,
            edges=edges,
            node_id=node_id,
            add_evidence=add_evidence,
            pivot_sources=pivot_sources,
            pivot_ranges=pivot_ranges,
        )
        self._extract_charts(
            sheet=sheet,
            value_sheet=value_sheet,
            source=source,
            content_hash=content_hash,
            document_id=document_id,
            sheet_id=sheet_id,
            nodes=nodes,
            edges=edges,
            node_id=node_id,
            add_evidence=add_evidence,
            chart_sources=chart_sources,
            drawing_manifest=drawing_manifest,
        )
        self._extract_text_boxes(
            sheet=sheet, source=source, content_hash=content_hash, document_id=document_id,
            sheet_id=sheet_id, nodes=nodes, edges=edges, node_id=node_id,
            add_evidence=add_evidence, drawing_manifest=drawing_manifest,
        )

    def _extract_text_boxes(
        self,
        *,
        sheet: Worksheet,
        source: Path,
        content_hash: str,
        document_id: str,
        sheet_id: str,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        node_id: Callable[[str, dict[str, str]], str],
        add_evidence: Callable[[str, str, EvidenceKind, str, SourceLocator], None],
        drawing_manifest: DrawingManifest,
    ) -> None:
        """Every text box on the sheet as text the sheet holds, placed by its drawn extent.

        Only one over a picture was kept, inside the picture's metadata, where no search found
        it; one over a chart or on its own was dropped. What a box lies on is binding's
        question, as for any text: one inside a chart or a picture goes under it there.
        """
        for item in drawing_manifest.objects:
            if item.worksheet != sheet.title or item.kind != "sp" or not item.text_runs:
                continue
            paragraphs: dict[int, str] = {}
            for run in item.text_runs:
                paragraphs[run["paragraph"]] = paragraphs.get(run["paragraph"], "") + run["text"]
            text = "\n".join(line for line in paragraphs.values() if line.strip()).strip()
            if not text:
                continue
            box_id = node_id("text_box", {"sheet": sheet.title, "object": item.object_id})
            locator = SourceLocator(
                file_name=source.name,
                label=item.name or "text box",
                sheet=sheet.title,
                cell_range=anchor_cell_range(item),
                source_part=item.drawing_part,
                metadata={"xml_index": item.xml_index},
            )
            nodes.append(GraphNode(id=box_id, document_id=document_id, type=NodeType.TEXT_CHUNK,
                                   label=text.splitlines()[0][:200], text=text, locator=locator))
            add_evidence(box_id, f"text_box:{sheet.title}:{item.object_id}",
                         EvidenceKind.SOURCE_TEXT, text, locator)
            edges.append(self._contains(content_hash, document_id, sheet_id, box_id))

    def _emit_table(
        self,
        *,
        sheet: Worksheet,
        value_sheet: Worksheet,
        source: Path,
        document_id: str,
        nodes: list[GraphNode],
        add_evidence: Callable[[str, str, EvidenceKind, str, SourceLocator], None],
        table_id: str,
        label: str,
        ref: str,
        metadata: dict[str, Any],
    ) -> _TableRange:
        locator = SourceLocator(
            file_name=source.name,
            label=label,
            sheet=sheet.title,
            cell_range=ref,
        )
        text, omitted = self._table_text(value_sheet, ref, fallback=sheet)
        if omitted:
            metadata = {**metadata, "omitted_rows": omitted}
        nodes.append(
            GraphNode(
                id=table_id,
                document_id=document_id,
                type=NodeType.TABLE,
                label=label,
                locator=locator,
                text=text,
                metadata=metadata,
            )
        )
        add_evidence(
            table_id,
            f"table:{sheet.title}:{ref}",
            EvidenceKind.SOURCE_VALUES,
            text,
            locator,
        )
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        return _TableRange(
            node_id=table_id,
            sheet=sheet.title,
            min_col=min_col,
            min_row=min_row,
            max_col=max_col,
            max_row=max_row,
        )

    def _visual_table_metadata(self, candidate: VisualTableCandidate) -> dict[str, Any]:
        def rectangle_data(rectangle: GridRectangle) -> dict[str, Any]:
            return {
                "range": rectangle.ref,
                "min_col": rectangle.min_col,
                "min_row": rectangle.min_row,
                "max_col": rectangle.max_col,
                "max_row": rectangle.max_row,
            }

        return {
            "table_kind": "visual",
            "range": candidate.ref,
            "column_groups": [
                {
                    **rectangle_data(group.rectangle),
                    "role": group.role,
                    "value_kind": group.value_kind,
                    "repeated_index": group.repeated_index,
                }
                for group in candidate.column_groups
            ],
            "row_bands": [
                {**rectangle_data(band.rectangle), "value_kind": band.value_kind}
                for band in candidate.row_bands
            ],
            "image_slots": [
                {
                    **rectangle_data(slot.rectangle),
                    "drawing_indices": list(slot.drawing_indices),
                }
                for slot in candidate.image_slots
            ],
            "merges": [rectangle_data(merge) for merge in candidate.merges],
            "drawings": [
                {
                    **rectangle_data(drawing.rectangle),
                    "index": drawing.index,
                    "bbox_px": [
                        drawing.left_px,
                        drawing.top_px,
                        drawing.right_px,
                        drawing.bottom_px,
                    ],
                }
                for drawing in candidate.drawings
            ],
            "signals": candidate.signals,
        }


    def _extract_native_text(
        self,
        *,
        sheet: Worksheet,
        cells: list[Cell],
        value_sheet: Worksheet,
        source: Path,
        content_hash: str,
        document_id: str,
        nodes: list[GraphNode],
        evidence: list[EvidenceRecord],
        node_id: Callable[[str, dict[str, str]], str],
        table_ranges: list[_TableRange],
    ) -> list[_LayoutUnit]:
        items: list[_LayoutUnit] = []
        merge_starts = {(m.min_row, m.min_col): m for m in sheet.merged_cells.ranges}
        for cell in cells:
            if isinstance(cell, MergedCell) or cell.value is None:
                continue
            if any(table_range.contains(cell.column, cell.row) for table_range in table_ranges):
                continue
            value = cell.value
            if is_formula(value):
                # A formula's words are what Excel last computed and saved beside it; one
                # never computed showed nothing on the sheet.
                value = value_sheet[cell.coordinate].value
                if value is None:
                    continue
            raw_text = str(value)
            text = raw_text.strip()
            if not text:
                continue
            merge = merge_starts.get((cell.row, cell.column))
            ref = merge.coord if merge is not None else cell.coordinate
            # A cell's text is text. A marker or a banner used to promote it to a
            # heading with a level, a confidence and reasons, and those decided the
            # tree every other unit on the sheet was hung from. What leads what is a
            # reading of the page, made by the pass that sees it.
            node_type = NodeType.TEXT_CHUNK
            text_id = node_id(
                node_type.value,
                {"sheet": sheet.title, "range": ref},
            )
            locator = SourceLocator(
                file_name=source.name,
                label=text,
                sheet=sheet.title,
                cell_range=ref,
            )
            nodes.append(
                GraphNode(
                    id=text_id,
                    document_id=document_id,
                    type=node_type,
                    label=text,
                    locator=locator,
                    text=text,
                    metadata={
                        "source_order": [cell.row, cell.column],
                        "merged_span": ref if merge is not None else None,
                        "font": {
                            "bold": bool(cell.font.bold),
                            "italic": bool(cell.font.italic),
                            "size": cell.font.sz,
                        },
                    },
                )
            )
            evidence.append(
                EvidenceRecord(
                    id=stable_id(content_hash, f"evidence:text:{sheet.title}:{ref}"),
                    document_id=document_id,
                    node_id=text_id,
                    kind=EvidenceKind.SOURCE_TEXT,
                    locator=locator,
                    text=text,
                )
            )
            items.append(
                _LayoutUnit(
                    cell.row, cell.column, text_id, "text", text=text, range_ref=ref
                )
            )
        return items

    def _extract_images(
        self,
        *,
        sheet: Worksheet,
        source: Path,
        content_hash: str,
        document_id: str,
        nodes: list[GraphNode],
        evidence: list[EvidenceRecord],
        node_id: Callable[[str, dict[str, str]], str],
        components: list[DrawingComponent],
        table_ranges: list[_TableRange],
        warnings: list[WarningRecord],
    ) -> list[_LayoutUnit]:
        if self._asset_store is None:
            return []
        items: list[_LayoutUnit] = []
        table_visual_emitted: set[str] = set()
        warned_components: set[str] = set()
        for component in components:
            for picture in component.pictures:
                image_id = node_id(
                    "image",
                    {
                        "sheet": sheet.title,
                        "drawing_part": picture.drawing_part,
                        "xml_index": str(picture.xml_index),
                        "nv_id": str(picture.nv_id),
                    },
                )
                ref = anchor_cell_range(picture)
                parent: str | None = None
                if ref is not None:
                    min_col, min_row, max_col, max_row = range_boundaries(ref)
                    image_rect = GridRectangle(
                        int(min_col), int(min_row), int(max_col), int(max_row)
                    )
                    matching_table_ids = [
                        table.node_id
                        for table in table_ranges
                        if image_rect.overlaps(table)
                    ]
                    if len(matching_table_ids) == 1:
                        parent = matching_table_ids[0]
                label = picture.name or f"Image {picture.xml_index + 1}"
                member_data = _drawing_member_data(component)
                locator = SourceLocator(
                    file_name=source.name,
                    label=label,
                    sheet=sheet.title,
                    cell_range=ref,
                    relationship_id=picture.media_relationship_id,
                    source_part=picture.drawing_part,
                    metadata={
                        "drawing_relationship_id": picture.drawing_relationship_id,
                        "xml_index": picture.xml_index,
                        "nv_id": picture.nv_id,
                    },
                )
                suffix = Path(picture.media_target or "image.png").suffix or ".bin"
                assert picture.media_bytes is not None
                asset_hash = self._asset_store.put(picture.media_bytes, suffix=suffix)
                nodes.append(
                    GraphNode(
                        id=image_id,
                        document_id=document_id,
                        type=NodeType.IMAGE,
                        label=label,
                        locator=locator,
                        metadata={
                            "anchor_range": ref,
                            "component_id": component.component_id,
                            "drawing_part": picture.drawing_part,
                            "source_fidelity": component.source_fidelity,
                            "members": member_data,
                            "contained_in_table": parent is not None,
                        },
                    )
                )
                mime_type = self._image_mime_type(suffix)
                evidence.append(
                    EvidenceRecord(
                        id=stable_id(content_hash, f"evidence:image:{picture.object_id}"),
                        document_id=document_id,
                        node_id=image_id,
                        kind=EvidenceKind.SOURCE_MEDIA,
                        locator=locator,
                        asset_hash=asset_hash,
                        mime_type=mime_type,
                        data={
                            "component_id": component.component_id,
                            "source_fidelity": component.source_fidelity,
                            "media_hash": picture.media_hash,
                            "members": member_data,
                        },
                    )
                )
                if (
                    component.warning is not None
                    and component.component_id not in warned_components
                ):
                    warnings.append(
                        WarningRecord(
                            id=stable_id(
                                content_hash, f"warn:drawing_composite:{component.component_id}"
                            ),
                            document_id=document_id,
                            code="drawing_composite_not_rendered",
                            message=component.warning,
                            severity=Severity.WARNING,
                            locator=locator,
                        )
                    )
                    warned_components.add(component.component_id)
                if parent is not None and parent not in table_visual_emitted:
                    evidence.append(
                        EvidenceRecord(
                            id=stable_id(content_hash, f"visual:{parent}"),
                            document_id=document_id,
                            node_id=parent,
                            kind=EvidenceKind.SOURCE_MEDIA,
                            locator=locator,
                            asset_hash=asset_hash,
                            mime_type=mime_type,
                            data={"component_id": component.component_id, "anchor_range": ref},
                        )
                    )
                    table_visual_emitted.add(parent)
                row = (
                    picture.anchor_from.row + 1
                    if picture.anchor_from and picture.anchor_from.row is not None
                    else 1
                )
                col = (
                    picture.anchor_from.col + 1
                    if picture.anchor_from and picture.anchor_from.col is not None
                    else 1
                )
                items.append(
                    _LayoutUnit(
                        row,
                        col,
                        image_id,
                        "image",
                        range_ref=ref,
                        parent_id=parent,
                    )
                )
        return items

    def _image_mime_type(self, suffix: str) -> str:
        extension = suffix.lower().removeprefix(".")
        return {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml",
            "tif": "image/tiff",
            "tiff": "image/tiff",
        }.get(extension, f"image/{extension}")

    def _emit_reading_hierarchy(
        self,
        *,
        content_hash: str,
        document_id: str,
        sheet_id: str,
        ordered_items: list[_LayoutUnit],
        nodes: list[GraphNode],
        first_node: int,
        edges: list[GraphEdge],
    ) -> None:
        """Hang every unit on what natively holds it, and nothing else.

        A drawing that is part of a group or a chart belongs to it; everything else belongs
        to its worksheet. This used to build a tree of headings out of the markers written
        in the cells and hang every table, picture and line under the deepest one whose
        region covered it -- the outline decided here, from display text, and kept in
        ownership where retrieval fell back on it whenever no derived outline existed.
        """
        # A unit's node is made while its sheet is read, so only the sheet's own are indexed.
        node_indexes = {
            nodes[index].id: index for index in range(first_node, len(nodes))
        }
        for unit in ordered_items:
            owner = unit.parent_id or sheet_id
            edges.append(self._contains(content_hash, document_id, owner, unit.item_id))
            node_index = node_indexes.get(unit.item_id)
            if node_index is None:
                continue
            node = nodes[node_index]
            # Where the unit sits is a fact of the sheet and orders the reading chain.
            nodes[node_index] = node.model_copy(
                update={
                    "metadata": {
                        **node.metadata,
                        "native_region": {"bbox": list(unit.bounds)},
                    }
                }
            )

    def _extract_charts(
        self,
        *,
        sheet: Worksheet,
        value_sheet: Worksheet,
        source: Path,
        content_hash: str,
        document_id: str,
        sheet_id: str,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        node_id: Callable[[str, dict[str, str]], str],
        add_evidence: Callable[[str, str, EvidenceKind, str, SourceLocator], None],
        chart_sources: list[tuple[str, list[str]]],
        drawing_manifest: DrawingManifest,
    ) -> None:
        frames = [
            item
            for item in drawing_manifest.objects
            # Top-level frames only: openpyxl does not read a chart inside a group.
            if item.worksheet == sheet.title
            and item.kind == "graphicFrame"
            and len(item.group_path) == 1
        ]
        for index, chart in enumerate(sheet._charts, start=1):
            label = self._title_text(chart.title) or f"Chart {index}"
            chart_id = node_id("chart", {"sheet": sheet.title, "index": str(index)})
            # Placed like a picture: the chart is the drawing frame anchored where openpyxl
            # read it, and the page shows it at that frame's anchor and extent.
            frame = self._chart_frame(chart, frames)
            locator = SourceLocator(
                file_name=source.name,
                label=label,
                sheet=sheet.title,
                cell_range=anchor_cell_range(frame) if frame is not None else None,
                source_part=frame.drawing_part if frame is not None else None,
                metadata={"xml_index": frame.xml_index} if frame is not None else {},
            )
            chart_family = chart.__class__.__name__.removesuffix("Chart").lower()
            chart_type = getattr(chart, "type", None)
            orientation = (
                {"col": "column", "bar": "bar"}.get(chart_type)
                if isinstance(chart_type, str)
                else None
            )
            series_metadata = []
            source_refs: list[str] = []
            for series in chart.ser:
                title_ref = (
                    series.tx.strRef.f
                    if series.tx is not None and series.tx.strRef is not None
                    else None
                )
                title = (
                    self._referenced_cell_value(value_sheet, title_ref, fallback=sheet)
                    if title_ref is not None
                    else getattr(series.tx, "v", None)
                )
                values = getattr(series, "val", None) or getattr(series, "yVal", None)
                values_ref = (
                    values.numRef.f if values is not None and values.numRef is not None else None
                )
                categories = getattr(series, "cat", None) or getattr(series, "xVal", None)
                categories_ref = None
                if categories is not None:
                    category_ref = categories.numRef or categories.strRef
                    categories_ref = category_ref.f if category_ref is not None else None
                normalized_values = self._normalize_chart_ref(values_ref)
                normalized_categories = self._normalize_chart_ref(categories_ref)
                series_metadata.append(
                    {
                        "title": title,
                        "values": self._referenced_range_values(
                            value_sheet, values_ref, fallback=sheet
                        ),
                        "categories": self._referenced_range_values(
                            value_sheet, categories_ref, fallback=sheet
                        ),
                        "values_ref": normalized_values,
                        "categories_ref": normalized_categories,
                    }
                )
                source_refs.extend(
                    ref for ref in (normalized_values, normalized_categories) if ref is not None
                )
            chart_sources.append((chart_id, source_refs))
            axes = {
                name: {"title": self._title_text(axis.title)}
                for name in ("x", "y")
                if (axis := getattr(chart, f"{name}_axis", None)) is not None
            }
            nodes.append(
                GraphNode(
                    id=chart_id,
                    document_id=document_id,
                    type=NodeType.CHART,
                    label=label,
                    locator=locator,
                    metadata={
                        "chart_family": chart_family,
                        "orientation": orientation,
                        "series": series_metadata,
                        "axes": axes,
                    },
                )
            )
            add_evidence(
                chart_id,
                f"chart:{sheet.title}:{index}",
                EvidenceKind.SOURCE_METADATA,
                f"chart {index} on worksheet {sheet.title}",
                locator,
            )
            edges.append(self._contains(content_hash, document_id, sheet_id, chart_id))
            # A box drawn inside the chart is kept in the chart's own drawing: the chart
            # holds it, natively, and it is placed where the chart is.
            for number, text in enumerate(frame.chart_shapes if frame is not None else (), 1):
                shape_id = node_id("chart_shape", {"chart": chart_id, "index": str(number)})
                shape_locator = locator.model_copy(update={"label": text.splitlines()[0][:200]})
                nodes.append(GraphNode(id=shape_id, document_id=document_id,
                                       type=NodeType.TEXT_CHUNK, label=shape_locator.label,
                                       text=text, locator=shape_locator))
                add_evidence(shape_id, f"chart_shape:{sheet.title}:{index}:{number}",
                             EvidenceKind.SOURCE_TEXT, text, shape_locator)
                edges.append(self._contains(content_hash, document_id, chart_id, shape_id))

    def _extract_pivots(
        self,
        *,
        sheet: Worksheet,
        value_sheet: Worksheet,
        source: Path,
        content_hash: str,
        document_id: str,
        sheet_id: str,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        node_id: Callable[[str, dict[str, str]], str],
        add_evidence: Callable[[str, str, EvidenceKind, str, SourceLocator], None],
        pivot_sources: list[_TableRange],
        pivot_ranges: list[_TableRange],
    ) -> None:
        # openpyxl exposes read-side pivots via the private `_pivots` list; each
        # carries the displayed region (location.ref) and its cache source. We
        # surface the cached/rendered region as a pivot-like table WITHOUT
        # recalculating; if no displayed values exist we keep metadata only and
        # mark the node non-retrievable rather than inventing values.
        for pivot in getattr(sheet, "_pivots", []):
            name = pivot.name or "PivotTable"
            ref = pivot.location.ref if pivot.location else None
            src = None
            ws_src = getattr(pivot.cache.cacheSource, "worksheetSource", None)
            if ws_src is not None and ws_src.ref:
                src = (ws_src.sheet or sheet.title, ws_src.ref)

            rendered = self._table_text(value_sheet, ref, fallback=sheet)[0] if ref else ""
            has_values = ref is not None and (
                self._region_has_values(value_sheet, ref) or self._region_has_values(sheet, ref)
            )

            pivot_id = node_id("pivot", {"sheet": sheet.title, "name": name})
            locator = SourceLocator(
                file_name=source.name,
                label=name,
                sheet=sheet.title,
                cell_range=ref,
            )
            metadata: dict[str, object] = {
                "values_available": has_values,
                "source_ref": f"{src[0]}!{src[1]}" if src else None,
            }
            nodes.append(
                GraphNode(
                    id=pivot_id,
                    document_id=document_id,
                    type=NodeType.PIVOT_TABLE,
                    label=name,
                    locator=locator,
                    text=rendered if has_values else None,
                    metadata=metadata,
                    retrievable=has_values,
                )
            )
            if has_values:
                add_evidence(
                    pivot_id,
                    f"pivot:{sheet.title}:{name}",
                    EvidenceKind.SOURCE_VALUES,
                    rendered,
                    locator,
                )
            else:
                add_evidence(
                    pivot_id,
                    f"pivot:{sheet.title}:{name}",
                    EvidenceKind.SOURCE_METADATA,
                    f"pivot {name} (cached values unavailable)",
                    locator,
                )
            edges.append(self._contains(content_hash, document_id, sheet_id, pivot_id))

            if ref is not None:
                min_col, min_row, max_col, max_row = range_boundaries(ref)
                pivot_ranges.append(
                    _TableRange(
                        node_id=pivot_id,
                        sheet=sheet.title,
                        min_col=min_col,
                        min_row=min_row,
                        max_col=max_col,
                        max_row=max_row,
                    )
                )

            if src is not None:
                src_sheet, src_ref = src
                min_col, min_row, max_col, max_row = range_boundaries(src_ref)
                pivot_sources.append(
                    _TableRange(
                        node_id=pivot_id,
                        sheet=src_sheet,
                        min_col=min_col,
                        min_row=min_row,
                        max_col=max_col,
                        max_row=max_row,
                    )
                )

    def _link_pivots(
        self,
        *,
        content_hash: str,
        document_id: str,
        edges: list[GraphEdge],
        table_ranges: list[_TableRange],
        pivot_sources: list[_TableRange],
    ) -> None:
        # A REFERENCES edge from a pivot to each recognized table its cache
        # source overlaps. Deduped by (pivot, table).
        seen: set[tuple[str, str]] = set()
        for ps in pivot_sources:
            for tr in table_ranges:
                if tr.sheet != ps.sheet:
                    continue
                if ps.overlaps(tr):
                    key = (ps.node_id, tr.node_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append(
                        GraphEdge(
                            id=stable_id(
                                content_hash,
                                f"references:{ps.node_id}->{tr.node_id}",
                            ),
                            document_id=document_id,
                            source_id=ps.node_id,
                            target_id=tr.node_id,
                            type=EdgeType.REFERENCES,
                        )
                    )

    def _link_formulas(  # type: ignore[no-untyped-def]
        self,
        *,
        wb,
        source: Path,
        content_hash: str,
        document_id: str,
        edges: list[GraphEdge],
        warnings: list[WarningRecord],
        table_ranges: list[_TableRange],
        sheet_ids: dict[str, str],
        defined_ranges: dict[str, str],
        sheet_cells: dict[str, list[Cell]],
    ) -> None:
        seen: set[tuple[str, str, str]] = set()  # the cell's sheet too: A1 is on every sheet
        for sheet in wb.worksheets:
            for cell in sheet_cells[sheet.title]:
                value = cell.value
                if isinstance(value, ArrayFormula):
                    value = value.text
                if not isinstance(value, str) or not value.startswith("="):
                    continue
                upper = value.upper()
                if any(fn in upper for fn in _DYNAMIC_FUNCS) or "[" in value:
                    # Dynamic (INDIRECT/OFFSET) and external-workbook refs
                    # cannot be resolved statically; warn and skip.
                    warnings.append(
                        WarningRecord(
                            id=stable_id(
                                content_hash,
                                f"warn:dynamic_ref:{sheet.title}!{cell.coordinate}",
                            ),
                            document_id=document_id,
                            code="unresolved_dynamic_reference",
                            message=(
                                f"skipped unresolved reference in "
                                f"{sheet.title}!{cell.coordinate}: {value}"
                            ),
                            severity=Severity.WARNING,
                            locator=SourceLocator(
                                file_name=source.name,
                                label=f"{sheet.title}!{cell.coordinate}",
                                sheet=sheet.title,
                                cell_range=cell.coordinate,
                            ),
                        )
                    )
                    continue
                if not table_ranges:
                    continue
                source_id = self._formula_source_id(
                    sheet.title, cell.column, cell.row, table_ranges, sheet_ids
                )
                expanded = self._expand_named_ranges(value, defined_ranges)
                for target in self._resolve_refs(expanded, table_ranges):
                    key = (sheet.title, cell.coordinate, target.node_id)
                    if source_id == target.node_id or key in seen:
                        continue
                    seen.add(key)
                    edges.append(
                        GraphEdge(
                            id=stable_id(
                                content_hash,
                                f"uses_value_from:{sheet.title}!{cell.coordinate}"
                                f"->{target.node_id}",
                            ),
                            document_id=document_id,
                            source_id=source_id,
                            target_id=target.node_id,
                            type=EdgeType.USES_VALUE_FROM,
                            locator=SourceLocator(
                                file_name=source.name,
                                label=f"{sheet.title}!{cell.coordinate}",
                                sheet=sheet.title,
                                cell_range=cell.coordinate,
                            ),
                            metadata={"formula": value},
                        )
                    )

    def _parse_defined_names(self, wb) -> dict[str, str]:  # type: ignore[no-untyped-def]
        # Map workbook-scoped defined names to their single A1 destination text
        # (e.g. RevenueRange -> "Sheet1!$A$1:$B$3"). Names whose value is not a
        # plain range (constants, multi-area, formulas) are left out; a formula
        # using them simply resolves no table, same as any unknown token.
        ranges: dict[str, str] = {}
        for name, defn in wb.defined_names.items():
            text = getattr(defn, "attr_text", None) or getattr(defn, "value", None)
            if not isinstance(text, str) or "!" not in text or "," in text:
                continue
            if _REF_RE.fullmatch(text):
                ranges[name.upper()] = text
        return ranges

    def _expand_named_ranges(self, formula: str, defined_ranges: dict[str, str]) -> str:
        if not defined_ranges:
            return formula

        # Replace whole-word name tokens with their range text so _resolve_refs
        # sees a normal Sheet!range reference. Word boundaries avoid clobbering
        # names embedded in longer identifiers.
        def repl(match: re.Match[str]) -> str:
            token = match.group(0)
            return defined_ranges.get(token.upper(), token)

        return re.sub(r"[^\W\d][\w.]*", repl, formula)

    def _formula_source_id(
        self,
        sheet_title: str,
        col: int,
        row: int,
        table_ranges: list[_TableRange],
        sheet_ids: dict[str, str],
    ) -> str:
        # If the formula cell sits inside a recognized table, that table owns the
        # dependency; otherwise the containing worksheet does.
        for tr in table_ranges:
            if tr.sheet == sheet_title and tr.contains(col, row):
                return tr.node_id
        return sheet_ids[sheet_title]

    def _resolve_refs(self, formula: str, table_ranges: list[_TableRange]) -> list[_TableRange]:
        matched: list[_TableRange] = []
        for m in _REF_RE.finditer(formula):
            sheet, ref = _ref_parts(m)
            referenced = GridRectangle(*range_boundaries(ref))
            for tr in table_ranges:
                if tr.sheet != sheet:
                    continue
                # Any overlap between referenced cells and the table body links.
                if referenced.overlaps(tr) and tr not in matched:
                    matched.append(tr)
        return matched

    @staticmethod
    def _chart_frame(chart: Any, frames: list[DrawingObject]) -> DrawingObject | None:
        """The drawing frame a chart was read from, found by the anchor openpyxl kept.

        openpyxl lists charts by anchor kind rather than in drawing order, so the position
        in `_charts` says nothing about which frame is which; the anchor does.
        """
        start = getattr(getattr(chart, "anchor", None), "_from", None)
        if start is None:
            return None
        found = [
            frame
            for frame in frames
            if frame.anchor_from is not None
            and (
                frame.anchor_from.col,
                frame.anchor_from.row,
                frame.anchor_from.col_off or 0,
                frame.anchor_from.row_off or 0,
            )
            == (start.col, start.row, start.colOff or 0, start.rowOff or 0)
        ]
        return found[0] if len(found) == 1 else None

    def _title_text(self, title: Any) -> str:
        if title is None or title.tx is None or title.tx.rich is None:
            return ""
        return "".join(run.t or "" for paragraph in title.tx.rich.p for run in paragraph.r)

    def _normalize_chart_ref(self, ref: str | None) -> str | None:
        if ref is None:
            return None
        match = _REF_RE.fullmatch(ref)
        if match is None:
            return ref
        sheet, cell_ref = _ref_parts(match, "$")
        return f"{sheet}!{cell_ref}"

    def _referenced_cell_value(
        self,
        sheet: Worksheet,
        ref: str | None,
        *,
        fallback: Worksheet | None = None,
    ) -> object:
        if ref is None:
            return None
        match = _REF_RE.fullmatch(ref)
        if match is None:
            return None
        sheet_name, cell_ref = _ref_parts(match)
        coordinate = cell_ref.partition(":")[0]
        value = sheet.parent[sheet_name][coordinate].value
        if value is None and fallback is not None:
            value = fallback.parent[sheet_name][coordinate].value
        return value

    def _referenced_range_values(
        self,
        sheet: Worksheet,
        ref: str | None,
        *,
        fallback: Worksheet | None = None,
    ) -> list[object]:
        if ref is None:
            return []
        match = _REF_RE.fullmatch(ref)
        if match is None:
            return []
        sheet_name, cell_ref = _ref_parts(match)
        target = sheet.parent[sheet_name]
        fallback_target = fallback.parent[sheet_name] if fallback is not None else None
        return [
            cell.value
            if cell.value is not None or fallback_target is None
            else fallback_target.cell(row=cell.row, column=cell.column).value
            # A range of one cell is one cell to `target[...]`, not rows of cells.
            for row in target.iter_rows(*_bounds(cell_ref))
            for cell in row
        ]

    def _region_has_values(self, sheet: Worksheet, ref: str) -> bool:
        # True only if at least one cell in the region carries a value; an empty
        # rectangle (a pivot whose cached region was never written to the grid)
        # has none, and must not be treated as a values-bearing pivot.
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        return any(
            cell.value is not None
            for (row, col), cell in sheet._cells.items()
            if min_row <= row <= max_row and min_col <= col <= max_col
        )

    def _table_text(
        self, sheet: Worksheet, ref: str, *, fallback: Worksheet | None = None
    ) -> tuple[str, int]:
        """The table's rows, a line each, and how many rows were left out between its head
        and its foot (`TABLE_HEAD_ROWS`, `TABLE_FOOT_ROWS`)."""
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        # A merged label/header repeats across the cells it spans in the table, instead
        # of leaving blanks in the non-anchor cells. Cells are read from the maps the
        # file filled: `sheet.cell()` would insert an empty one for each miss.
        merged: dict[tuple[int, int], object] = {}
        for mc in sheet.merged_cells.ranges:
            anchor = sheet._cells.get((mc.min_row, mc.min_col))
            if anchor is None or anchor.value is None:
                continue
            for r in range(max(mc.min_row, min_row), min(mc.max_row, max_row) + 1):
                for c in range(max(mc.min_col, min_col), min(mc.max_col, max_col) + 1):
                    merged[(r, c)] = anchor.value

        def value_at(worksheet: Worksheet, row: int, col: int) -> object:
            cell = worksheet._cells.get((row, col))
            return None if cell is None else cell.value

        def line(row: int) -> str:
            cells = []
            for col in range(min_col, max_col + 1):
                value = merged.get((row, col), value_at(sheet, row, col))
                if value is None and fallback is not None:
                    value = value_at(fallback, row, col)
                    if isinstance(value, ArrayFormula):  # never computed: its formula, as text
                        value = value.text
                    elif is_formula(value) and not isinstance(value, str):
                        value = None
                cells.append("" if value is None else str(value))
            return " | ".join(cells)

        rows = range(min_row, max_row + 1)
        if len(rows) <= LONG_TABLE_ROWS:
            return "\n".join(map(line, rows)), 0
        omitted = len(rows) - TABLE_HEAD_ROWS - TABLE_FOOT_ROWS
        head = map(line, rows[:TABLE_HEAD_ROWS])
        foot = map(line, rows[-TABLE_FOOT_ROWS:])
        return "\n".join([*head, f"… {omitted} rows omitted …", *foot]), omitted

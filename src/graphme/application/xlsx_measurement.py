from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.cell import coordinate_to_tuple

from graphme.adapters.extractors.excel import open_workbook
from graphme.adapters.extractors.xlsx_drawings import (
    DrawingObject,
    parse_xlsx_drawings,
)

# DrawingML marker offsets and one-cell extents use EMUs. These conservative
# nominal cell units are used only to turn an extent into a covering cell range;
# raw EMU values are retained in the report for auditability.
_EMU_PER_NOMINAL_COLUMN = 609_600
_EMU_PER_NOMINAL_ROW = 190_500


@dataclass(frozen=True, slots=True)
class CellRange:
    min_row: int
    max_row: int
    min_col: int
    max_col: int

    @classmethod
    def from_a1(cls, value: str) -> CellRange:
        parts = value.split(":")
        if len(parts) == 1:
            parts.append(parts[0])
        if len(parts) != 2:
            raise ValueError(f"Invalid A1 cell range: {value}")
        min_row, min_col = coordinate_to_tuple(parts[0])
        max_row, max_col = coordinate_to_tuple(parts[1])
        if min_row < 1 or min_col < 1 or max_row < 1 or max_col < 1:
            raise ValueError(f"Invalid A1 cell range: {value}")
        if min_row > max_row or min_col > max_col:
            raise ValueError(f"Reversed A1 cell range: {value}")
        return cls(min_row, max_row, min_col, max_col)

    def include(self, other: CellRange) -> CellRange:
        return CellRange(
            min(self.min_row, other.min_row),
            max(self.max_row, other.max_row),
            min(self.min_col, other.min_col),
            max(self.max_col, other.max_col),
        )

    @property
    def a1(self) -> str:
        start = f"{get_column_letter(self.min_col)}{self.min_row}"
        end = f"{get_column_letter(self.max_col)}{self.max_row}"
        return start if start == end else f"{start}:{end}"


@dataclass(frozen=True, slots=True)
class RangeContributor:
    source: str
    detail: str
    range: str


@dataclass(frozen=True, slots=True)
class SheetMeasurement:
    index: int
    name: str
    worksheet_part: str
    dimension: str | None
    cell_content_range: str | None
    drawing_content_range: str | None
    visual_content_range: str | None
    cell_count: int
    drawing_count: int
    contributors: tuple[RangeContributor, ...]


@dataclass(frozen=True, slots=True)
class WorkbookMeasurement:
    source: str
    sha256: str
    sheets: tuple[SheetMeasurement, ...]
    warnings: tuple[str, ...]


def _range_from_cells(cells: Iterable[tuple[int, int]]) -> CellRange | None:
    values = list(cells)
    if not values:
        return None
    rows, cols = zip(*values, strict=True)
    return CellRange(min(rows), max(rows), min(cols), max(cols))


def _drawing_range(item: DrawingObject) -> CellRange | None:
    start = item.anchor_from
    if start is None or start.col is None or start.row is None:
        return None
    min_col = start.col + 1
    min_row = start.row + 1
    if (
        item.anchor_to is not None
        and item.anchor_to.col is not None
        and item.anchor_to.row is not None
    ):
        return CellRange(min_row, item.anchor_to.row + 1, min_col, item.anchor_to.col + 1)
    if item.extent is not None and item.extent.cx is not None and item.extent.cy is not None:
        # A true one-cell anchor has no `to`; ceil to a covering nominal cell.
        col_span = max(1, (item.extent.cx + _EMU_PER_NOMINAL_COLUMN - 1) // _EMU_PER_NOMINAL_COLUMN)
        row_span = max(1, (item.extent.cy + _EMU_PER_NOMINAL_ROW - 1) // _EMU_PER_NOMINAL_ROW)
        return CellRange(min_row, min_row + row_span - 1, min_col, min_col + col_span - 1)
    return CellRange(min_row, min_row, min_col, min_col)


def _with_range(
    items: list[RangeContributor],
    current: CellRange | None,
    source: str,
    detail: str,
    value: CellRange,
) -> CellRange:
    items.append(RangeContributor(source, detail, value.a1))
    return value if current is None else current.include(value)


def measure_workbook(source: Path) -> WorkbookMeasurement:
    source = source.resolve(strict=True)
    workbook = open_workbook(source, read_only=False, data_only=False)
    drawings = parse_xlsx_drawings(source)
    by_sheet: dict[str, list[DrawingObject]] = {}
    for item in drawings.objects:
        by_sheet.setdefault(item.worksheet, []).append(item)
    sheets: list[SheetMeasurement] = []
    warnings = list(drawings.warnings)
    try:
        for index, sheet in enumerate(workbook.worksheets):
            contributors: list[RangeContributor] = []
            cell_bounds = _range_from_cells(
                (cell.row, cell.column)
                for cell in sheet._cells.values()
                if cell.value is not None
            )
            if cell_bounds is not None:
                contributors.append(
                    RangeContributor(
                        "cells", "non-empty cell values/formulas", cell_bounds.a1
                    )
                )
            visual_bounds = cell_bounds
            for merged in sheet.merged_cells.ranges:
                value = CellRange(merged.min_row, merged.max_row, merged.min_col, merged.max_col)
                visual_bounds = _with_range(
                    contributors, visual_bounds, "merged", str(merged), value
                )
            for table in sheet.tables.values():
                min_col, min_row, max_col, max_row = range_boundaries(table.ref)
                value = CellRange(min_row, max_row, min_col, max_col)
                visual_bounds = _with_range(contributors, visual_bounds, "table", table.name, value)
            sheet_drawings = by_sheet.get(sheet.title, [])
            drawing_bounds: CellRange | None = None
            for item in sheet_drawings:
                drawing_value = _drawing_range(item)
                if drawing_value is None:
                    warnings.append(f"unmeasurable drawing {item.object_id} on {sheet.title}")
                    continue
                drawing_bounds = _with_range(
                    contributors,
                    drawing_bounds,
                    "drawing",
                    f"{item.kind}:{item.name or item.object_id}",
                    drawing_value,
                )
                visual_bounds = (
                    drawing_value
                    if visual_bounds is None
                    else visual_bounds.include(drawing_value)
                )
            sheets.append(
                SheetMeasurement(
                    index=index,
                    name=sheet.title,
                    worksheet_part=next((item.worksheet_part for item in sheet_drawings), ""),
                    dimension=sheet.calculate_dimension(),
                    cell_content_range=cell_bounds.a1 if cell_bounds else None,
                    drawing_content_range=drawing_bounds.a1 if drawing_bounds else None,
                    visual_content_range=visual_bounds.a1 if visual_bounds else None,
                    cell_count=sum(cell.value is not None for cell in sheet._cells.values()),
                    drawing_count=len(sheet_drawings),
                    contributors=tuple(contributors),
                )
            )
    finally:
        workbook.close()
    return WorkbookMeasurement(
        source=str(source),
        sha256=drawings.workbook_hash,
        sheets=tuple(sheets),
        warnings=tuple(warnings),
    )


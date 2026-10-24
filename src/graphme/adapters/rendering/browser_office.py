"""Render OOXML documents to page images with a headless browser, not an Office install.

The OOXML viewer parses the file in WebAssembly and draws to a canvas, so a worksheet
renders whole instead of being paginated onto A4 sheets. That pagination is what broke
the previous route: a workbook of five sheets became forty-four printed pages, and
nothing could be said about which sheet a page belonged to.

PDF is not routed here -- it is already page images and pymupdf rasterises it directly.
"""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graphme import processes
from graphme.adapters.rendering.xlsx_measurement import CellRange, measure_workbook
from graphme.application.page_regions import PixelBox, SheetPixelGrid
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import SourceLocator

BROWSER_FORMATS: dict[DocumentFormat, str] = {
    DocumentFormat.DOCX: "docx",
    DocumentFormat.PPTX: "pptx",
    DocumentFormat.XLSX: "xlsx",
}
DEFAULT_WIDTH = 1400
DEFAULT_DEVICE_PIXEL_RATIO = 2.0
PREVIEW_DEVICE_PIXEL_RATIO = 1.0
"""A page drawn for a reader, not read: a quarter of the reading's pixels."""
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MARGIN_PIXELS = 64
"""Real neighbouring cells drawn outside the measured range, so a range that
under-reached shows what it missed instead of hiding it."""
DEFAULT_WHITE_MARGIN_PIXELS = 32
"""Blank border outside the cells, so no unit sits flush against the frame."""


def build_xlsx_plan(
    source: Path,
    *,
    margin_pixels: int = DEFAULT_MARGIN_PIXELS,
    white_margin_pixels: int = DEFAULT_WHITE_MARGIN_PIXELS,
    scale: float = DEFAULT_DEVICE_PIXEL_RATIO,
) -> dict[str, Any]:
    """Measure each worksheet's content range; the viewer exposes no used-range."""
    if margin_pixels < 0 or white_margin_pixels < 0:
        raise ValueError("margins must not be negative")
    if scale <= 0:
        raise ValueError("render scale must be positive")
    measurement = measure_workbook(source)
    sheets = []
    for sheet in measurement.sheets:
        if sheet.visual_content_range is None:
            continue  # an empty worksheet has nothing to draw
        bounds = CellRange.from_a1(sheet.visual_content_range)
        sheets.append({
            "index": sheet.index,
            "name": sheet.name,
            "range": sheet.visual_content_range,
            "min_row": bounds.min_row, "max_row": bounds.max_row,
            "min_col": bounds.min_col, "max_col": bounds.max_col,
        })
    return {
        "schema_version": 1,
        "render_scale": scale,
        "margin_pixels": margin_pixels,
        "white_margin_pixels": white_margin_pixels,
        "warnings": list(measurement.warnings),
        "sheets": sheets,
    }


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One rendered page, slide or worksheet."""

    index: int
    width: int
    height: int
    png: bytes
    label: str | None = None
    """Worksheet name, where the format has one."""
    column_edges: dict[int, int] | None = None
    row_edges: dict[int, int] | None = None
    scale: float | None = None
    device_pixel_ratio: float | None = None
    text_runs: tuple[dict[str, Any], ...] = ()
    """Where each shape's words actually drew, as the renderer reports them."""
    elements: tuple[dict[str, Any], ...] = ()
    """Drawings a flow document laid out: a picture or a floating shape, with the body
    element it is anchored to. A flow document states no coordinates of its own."""

    def flow_boxes(self) -> dict[tuple[str, str, int], PixelBox]:
        """One box per laid-out element of a flow document, keyed by where it came from.

        A paragraph is one body index; a table is the same index reached through its
        cells, so grouping on it gives the table whole. Drawings carry the same key and
        are measured from their own bounds, having no words to be found by.

        A run with nothing written in it is not where the words are. An inline picture
        sits in a line of text, and the spaces around it come back as runs the height of
        that line: measured with them, the paragraph covered the picture beside it and
        everything the picture then merged with.
        """
        boxes: dict[tuple[str, str, int], PixelBox] = {}
        for item in self.text_runs:
            if not str(item.get("text", "")).strip():
                continue
            key, box = _flow_item(item)
            if key is None or box is None:
                continue
            boxes[key] = box if key not in boxes else boxes[key].union(box)
        return boxes

    def flow_drawings(self, kind: str = "image") -> dict[tuple[str, str, int], list[PixelBox]]:
        """Each drawing a flow document laid out, under the body element it hangs from.

        A drawing is a unit of its own and is kept apart from the words of the paragraph
        it is anchored to: unioned with them, that paragraph took the picture's rectangle
        and swallowed whatever else stood in the same band.

        Only the pictures. The layout reports a paragraph's text boxes here too, and taken
        as drawings the first of them became the picture's box -- a figure's node carried
        the rectangle of the label drawn under it, and the figure belonged to nothing. A
        box of words is found by the words in it, like any other text.

        `kind` "chart" gives the charts instead, each counted among its own kind the way
        the extraction counts them.
        """
        found: dict[tuple[str, str, int], list[tuple[int, PixelBox]]] = {}
        for item in self.elements:
            if item.get("kind") != kind:
                continue
            key, box = _flow_item(item)
            path = item.get("path")
            if key is None or box is None or not isinstance(path, list):
                continue
            within = path[1] if len(path) > 1 and isinstance(path[1], int) else 0
            found.setdefault(key, []).append((within, box))
        return {
            key: [box for _order, box in sorted(items, key=lambda pair: pair[0])]
            for key, items in found.items()
        }

    def glyph_boxes(self) -> dict[str, PixelBox]:
        """One box per shape, covering the words it drew.

        A run inside a group is reported in that group's own coordinate space, which puts
        it somewhere other than where it drew -- sometimes off the page entirely. Those
        are left out, and the shape keeps the box the file gives it.
        """
        drawn: dict[str, PixelBox] = {}
        outside: set[str] = set()
        for run in self.text_runs:
            shape = run.get("shapeId")
            if shape is None:
                continue
            key = str(shape)
            box = PixelBox(
                x0=float(run["x"]),
                y0=float(run["y"]),
                x1=float(run["x"]) + float(run["w"]),
                y1=float(run["y"]) + float(run["h"]),
            )
            if (
                box.x0 < 0
                or box.y0 < 0
                or box.x1 > self.width
                or box.y1 > self.height
            ):
                outside.add(key)
                continue
            drawn[key] = box if key not in drawn else drawn[key].union(box)
        return {key: box for key, box in drawn.items() if key not in outside}

    def grid(self) -> SheetPixelGrid | None:
        """Where each column and row boundary landed, for cropping by lookup."""
        if not self.column_edges or not self.row_edges:
            return None
        return SheetPixelGrid(
            column_edges=self.column_edges,
            row_edges=self.row_edges,
            scale=self.scale or 1.0,
            device_pixel_ratio=self.device_pixel_ratio or 1.0,
        )


@dataclass(frozen=True, slots=True)
class BrowserRenderResult:
    pages: tuple[RenderedPage, ...]
    mappings: tuple[SourcePageMapping, ...]


@dataclass(frozen=True, slots=True)
class SourcePageMapping:
    locator: SourceLocator
    page_start: int
    page_end: int


CaptureCallable = Callable[[Path, str, Path], list[RenderedPage]]


def _run_capture(
    viewer_directory: Path,
    chrome_executable: str | None,
    width: int,
    device_pixel_ratio: float,
    timeout_seconds: float,
) -> CaptureCallable:
    def capture(source: Path, kind: str, workspace: Path) -> list[RenderedPage]:
        import os
        import sys

        # Its messages carry file names: UTF-8 both ways, not Windows' ANSI code page.
        environment = {**os.environ, "DIST_ROOT": str(viewer_directory),
                       "PYTHONIOENCODING": "utf-8"}
        if chrome_executable:
            environment["BROWSER_PATH"] = chrome_executable
        completed = processes.run(
            [
                sys.executable, "-m", "graphme.adapters.rendering.browser_capture",
                kind, str(source), str(workspace), str(width), str(device_pixel_ratio),
            ],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds, env=environment,
        )
        if completed.returncode != 0:
            # Surface the browser's own message; a bare exit code says nothing usable.
            detail = (completed.stderr.strip() or completed.stdout.strip())[:1_500]
            raise RuntimeError(detail or f"the renderer exited with {completed.returncode}")
        manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
        return [
            RenderedPage(
                index=entry["index"] + 1,
                width=entry["width"],
                height=entry["height"],
                png=(workspace / f"page-{entry['index'] + 1:03d}.png").read_bytes(),
                label=entry.get("label"),
                column_edges=(
                    {int(k): v for k, v in entry["columnEdges"].items()}
                    if entry.get("columnEdges")
                    else None
                ),
                row_edges=(
                    {int(k): v for k, v in entry["rowEdges"].items()}
                    if entry.get("rowEdges")
                    else None
                ),
                scale=entry.get("scale"),
                device_pixel_ratio=entry.get("dpr"),
                text_runs=tuple(entry.get("textRuns") or ()),
                elements=tuple(entry.get("elements") or ()),
            )
            for entry in manifest["pages"]
        ]

    return capture


class BrowserOfficeRenderer:
    """Render DOCX, PPTX and XLSX through the bundled OOXML viewer."""

    def __init__(
        self,
        *,
        viewer_directory: Path,
        capture: CaptureCallable | None = None,
        chrome_executable: str | None = None,
        width: int = DEFAULT_WIDTH,
        device_pixel_ratio: float = DEFAULT_DEVICE_PIXEL_RATIO,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        margin_pixels: int = DEFAULT_MARGIN_PIXELS,
        white_margin_pixels: int = DEFAULT_WHITE_MARGIN_PIXELS,
    ) -> None:
        if width <= 0:
            raise ValueError("render width must be positive")
        if device_pixel_ratio <= 0:
            raise ValueError("device pixel ratio must be positive")
        if timeout_seconds <= 0:
            raise ValueError("render timeout must be positive")
        self._device_pixel_ratio = device_pixel_ratio
        self._margin_pixels = margin_pixels
        self._white_margin_pixels = white_margin_pixels
        self._capture = capture or _run_capture(
            viewer_directory,
            chrome_executable,
            width,
            device_pixel_ratio,
            timeout_seconds,
        )

    def render(self, source: Path, document_format: DocumentFormat) -> tuple[RenderedPage, ...]:
        return self._draw(source, document_format, scale=self._device_pixel_ratio)

    def preview(self, source: Path, document_format: DocumentFormat, index: int,
                device_pixel_ratio: float = PREVIEW_DEVICE_PIXEL_RATIO) -> bytes:
        """Page `index` (from 1) alone, for a reader to look at: drawn at `device_pixel_ratio`,
        with nothing measured on it."""
        view = {"only": index - 1, "dpr": device_pixel_ratio}
        drawn = self._draw(source, document_format, scale=device_pixel_ratio, view=view)
        return drawn[0].png

    def _draw(self, source: Path, document_format: DocumentFormat, *, scale: float,
              view: dict[str, float] | None = None) -> tuple[RenderedPage, ...]:
        kind = BROWSER_FORMATS.get(document_format)
        if kind is None:
            raise ValueError(
                f"{document_format.value} is not handled by the browser renderer"
            )
        with tempfile.TemporaryDirectory(prefix="graphme-browser-") as temporary:
            workspace = Path(temporary)
            if view is not None:
                (workspace / "view.json").write_text(json.dumps(view), encoding="utf-8")
            if document_format is DocumentFormat.XLSX:
                # The viewer has no used-range; the plan carries what the file says.
                (workspace / "plan.json").write_text(
                    json.dumps(
                        build_xlsx_plan(
                            source,
                            margin_pixels=self._margin_pixels,
                            # Stated at the reading's resolution; a preview's is smaller.
                            white_margin_pixels=round(
                                self._white_margin_pixels * scale / self._device_pixel_ratio),
                            scale=scale,
                        ),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            if document_format is DocumentFormat.DOCX:
                staged = _without_vml_copies(source, workspace)
            else:
                staged = source
            try:
                pages = self._capture(staged, kind, workspace)
            except Exception as error:
                raise RuntimeError(f"browser render failed for {source}: {error}") from error
        if not pages:
            raise RuntimeError(f"browser render produced no pages for {source}")
        return tuple(pages)

    def render_with_mapping(
        self, source: Path, document_format: DocumentFormat
    ) -> BrowserRenderResult:
        pages = self.render(source, document_format)
        return BrowserRenderResult(
            pages=pages, mappings=tuple(self._map(source, document_format, pages))
        )

    @staticmethod
    def _map(
        source: Path, document_format: DocumentFormat, pages: tuple[RenderedPage, ...]
    ) -> list[SourcePageMapping]:
        mappings = []
        for page in pages:
            locator = SourceLocator(
                file_name=source.name,
                label=page.label or f"page {page.index}",
                page=page.index,
                sheet=page.label if document_format is DocumentFormat.XLSX else None,
                slide=page.index if document_format is DocumentFormat.PPTX else None,
            )
            # One page per sheet or slide, so a span is always a single page.
            mappings.append(SourcePageMapping(locator, page.index, page.index))
        return mappings


_GFXDATA = re.compile(rb'\s+o:gfxdata="[^"]*"')


def _without_vml_copies(source: Path, workspace: Path) -> Path:
    """The file the viewer is given, without `o:gfxdata`.

    Word writes a drawing twice: DrawingML in `mc:Choice`, and a VML `mc:Fallback` whose
    `o:gfxdata` is a base64 copy of the same drawing. The viewer draws the choice, but it
    still reads the fallback, and refuses any single XML event over its hard (compiled-in)
    limit -- so one large copy failed the whole document. The source is not touched; a
    file without the attribute is passed as it is.
    """
    try:
        with zipfile.ZipFile(source) as archive:
            found = any(
                b"o:gfxdata=" in archive.read(item)
                for item in archive.infolist()
                if item.filename.endswith(".xml")
            )
    except (OSError, zipfile.BadZipFile):
        return source  # what is wrong with it is the viewer's to say
    if not found:
        return source
    staged = workspace / "source" / source.name
    staged.parent.mkdir()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(staged, "w") as copy:
        for item in archive.infolist():
            data = archive.read(item)
            if item.filename.endswith(".xml"):
                data = _GFXDATA.sub(b"", data)
            copy.writestr(item, data)
    return staged


def _flow_item(
    item: Mapping[str, Any],
) -> tuple[tuple[str, str, int] | None, PixelBox | None]:
    """Where one reported run or drawing came from, and the box it drew in."""
    path, story = item.get("path"), item.get("story")
    if not isinstance(path, list) or not path or story is None:
        return None, None
    index = path[0]
    if not isinstance(index, int) or isinstance(index, bool):
        return None, None
    return (str(story), str(item.get("instance")), index), PixelBox(
        x0=float(item["x"]),
        y0=float(item["y"]),
        x1=float(item["x"]) + float(item["w"]),
        y1=float(item["y"]) + float(item["h"]),
    )

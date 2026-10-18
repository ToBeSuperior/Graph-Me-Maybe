from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pymupdf

from graphme.application.text_match import encodable
from graphme.domain.enums import (
    DocumentFormat,
    EdgeType,
    EvidenceKind,
    NodeType,
    Severity,
)
from graphme.domain.ids import canonical_locator, sha256_file, stable_id
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    JsonValue,
    SourceLocator,
    WarningRecord,
    native_edge,
)
from graphme.ports.storage import AssetStore


@dataclass(frozen=True, slots=True)
class _NativeUnit:
    page: int
    occurrence: int
    type: NodeType
    label: str
    text: str | None
    bbox: tuple[float, float, float, float]
    metadata: dict[str, JsonValue]
    evidence_kind: EvidenceKind
    evidence_data: JsonValue = None
    asset_hash: str | None = None
    mime_type: str | None = None


WORD_GAP = 0.15
"""A gap between two glyphs wider than this share of the font size is a space. Some PDFs write
no space character at all and only move the pen -- a Korean report drew every word apart with
nothing between them -- and pymupdf then runs the words together."""


TRACKED = 0.08
"""A line whose glyphs mostly stand this share of the font size apart or more is set with its
letters spaced out -- a title in capitals, "I N T R O D U C T I O N". pymupdf takes those gaps
for spaces; measured from the line's own spacing instead, only a word's gap is one."""
TRACKED_LETTERS = 7
"""Fewer letters than this -- "1 − ρ2 H", "(B-A)" -- are too few to tell spacing from words."""
TRACKING_SPREAD = 0.03
"""How close to the line's usual gap two thirds of its gaps have to be for it to be spaced out
rather than a line whose words merely stand apart (a Korean line written without spaces)."""


def page_text(page: pymupdf.Page, **options: Any) -> dict[str, Any]:
    """`page.get_text("dict")`, with a space wherever the page moved the pen a word's gap
    without writing one (`WORD_GAP`), and none between the letters of a spaced-out line
    (`TRACKED`). Every read of a page's text goes through here, so a unit, its printed lines
    and the words the document writes all agree.

    Boxes are in the page as it is shown, as `page.rect`, the rendered page and the table
    finder have them: pymupdf reports text on a page with /Rotate in the unturned page."""
    turned = bool(page.rotation)
    sort = bool(options.pop("sort", False)) if turned else False
    if turned and options.get("clip") is not None:
        options["clip"] = pymupdf.Rect(options["clip"]) * page.derotation_matrix  # type: ignore[no-untyped-call]
    raw: dict[str, Any] = page.get_text("rawdict", **options)  # type: ignore[no-untyped-call]
    if turned:
        _turn(raw, page.rotation_matrix)
        if sort:
            raw["blocks"].sort(key=lambda b: (b["bbox"][3], b["bbox"][0]))
    for block in raw.get("blocks", ()):
        for line in block.get("lines", ()):
            glyphs = [(char, float(span.get("size", 0.0)) or 1.0)
                      for span in line.get("spans", ()) for char in span.get("chars", ())]
            track = _tracking(glyphs)
            dropped: set[int] = set()
            if track:
                for i in range(1, len(glyphs) - 1):
                    (before, size), (char, _), (after, _) = glyphs[i - 1 : i + 2]
                    if (str(char["c"]) == " " and not _blank(before) and not _blank(after)
                            and _gap(before, after, size) - track < WORD_GAP):
                        dropped.add(id(char))
            last: tuple[float, str] | None = None  # right edge and character of the glyph before
            for span in line.get("spans", ()):
                size = float(span.get("size", 0.0)) or 1.0
                text: list[str] = []
                for char in span.pop("chars", ()):
                    if id(char) in dropped:
                        continue
                    c = str(char["c"])
                    if (last is not None and not c.isspace() and not last[1].isspace()
                            and (float(char["bbox"][0]) - last[0]) / size - track > WORD_GAP):
                        text.append(" ")
                    text.append(c)
                    last = (float(char["bbox"][2]), c)
                span["text"] = encodable("".join(text))
    return raw


def _turn(raw: dict[str, Any], matrix: Any) -> None:
    """Every box, origin and writing direction in `raw`, carried by `matrix`."""
    turn = pymupdf.Matrix(matrix.a, matrix.b, matrix.c, matrix.d, 0, 0)  # type: ignore[no-untyped-call]
    for block in raw.get("blocks", ()):
        parts = [block]
        for line in block.get("lines", ()):
            line["dir"] = tuple(pymupdf.Point(line["dir"]) * turn)  # type: ignore[no-untyped-call]
            parts.append(line)
            for span in line.get("spans", ()):
                parts += [span, *span.get("chars", ())]
        for part in parts:
            part["bbox"] = tuple(pymupdf.Rect(part["bbox"]) * matrix)  # type: ignore[no-untyped-call]
            if "origin" in part:
                part["origin"] = tuple(pymupdf.Point(part["origin"]) * matrix)  # type: ignore[no-untyped-call]


def _blank(char: dict[str, Any]) -> bool:
    return str(char["c"]).isspace()


def _gap(before: dict[str, Any], after: dict[str, Any], size: float) -> float:
    return (float(after["bbox"][0]) - float(before["bbox"][2])) / size


def _tracking(glyphs: list[tuple[dict[str, Any], float]]) -> float:
    """How far apart a spaced-out line sets its letters, as a share of the font size; 0 for a
    line set as usual.

    The gaps are measured letter to letter, across a space between them as well. A spaced-out
    line is one pymupdf read half as words: it took some of its even gaps for spaces and not
    others ("I NT RO DUC T I ON"). A line with a space between every two letters -- "1 2 3 4 5
    6 7", "a + b = c" -- or none is written that way, and is left as written."""
    at = [i for i, (char, _) in enumerate(glyphs) if not _blank(char)]
    pairs = list(zip(at, at[1:], strict=False))
    if len(pairs) < TRACKED_LETTERS - 1:
        return 0.0
    spaced = sum(after - before > 1 for before, after in pairs)
    if spaced < 2 or len(pairs) - spaced < 2:
        return 0.0
    gaps = [_gap(glyphs[before][0], glyphs[after][0], glyphs[after][1]) for before, after in pairs]
    usual = statistics.median(gaps)
    if usual < TRACKED or 3 * sum(abs(g - usual) <= TRACKING_SPREAD for g in gaps) < 2 * len(gaps):
        return 0.0
    # And the letters pymupdf ran together stand as far apart as the rest: in "a b cd ef" they
    # touch, and the even gaps are the spaces the line was written with.
    solid = [gap for gap, (before, after) in zip(gaps, pairs, strict=True) if after - before == 1]
    return usual if abs(statistics.median(solid) - usual) <= TRACKING_SPREAD else 0.0


FRAME_PAGE_SHARE = 0.15
"""How much of the page a detected table has to cover before it is worth asking whether it
is a border rather than a table. A frame need not fill the page -- a report is drawn inside
the margins, and often inside a second frame within the first."""
FRAME_FILL_RATIO = 0.5
"""A real table is mostly filled in; a border is mostly empty space between rules."""
FRAME_CELL_CHARS = 200
"""A cell holding this much text is a region of the page, not a value."""
FRAME_DEPTH = 3
"""How many borders deep to look. A report is drawn inside one frame, sometimes two."""
FRAME_INSET = 2.0
"""How far inside a frame's own edges to search, so the frame is not found again."""
HIGHLIGHT_LINE_HEIGHTS = 2.0
"""A one-row table no taller than this many times the text it holds is a line of text with
something painted behind it, not a table."""
"""A cell holding this much text is a region of the page, not a value."""


class PDFExtractor:
    """Build an authoritative semantic graph directly from PDF native objects."""

    format = DocumentFormat.PDF

    def __init__(self, *, asset_store: AssetStore | None = None) -> None:
        self._asset_store = asset_store

    def extract(self, source: Path) -> ExtractedDocument:
        content_hash = sha256_file(source)
        document_id = stable_id(
            content_hash, canonical_locator("document", {"file": source.name})
        )
        document_locator = SourceLocator(file_name=source.name, label=source.name)
        document_node = GraphNode(
            id=document_id,
            document_id=document_id,
            type=NodeType.DOCUMENT,
            label=source.name,
            locator=document_locator,
            content_hash=content_hash,
            retrievable=False,
        )
        evidence: list[EvidenceRecord] = [
            EvidenceRecord(
                id=stable_id(content_hash, "evidence:document"),
                document_id=document_id,
                node_id=document_id,
                kind=EvidenceKind.SOURCE_METADATA,
                locator=document_locator,
                text=f"PDF document {source.name}",
            )
        ]
        warnings: list[WarningRecord] = []
        units: list[_NativeUnit] = []
        page_count = 0
        native_text_pages = 0

        # Parser failures are intentionally allowed to propagate. Native extraction must
        # never turn a corrupt/unsupported PDF into an unannounced vision fallback.
        with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
            page_count = document.page_count
            if page_count == 0:
                raise ValueError("PDF must contain at least one page")
            # A break at the margin leaves no mark saying whether a word was cut or a
            # space was eaten. The rest of the document is the only evidence there is.
            # Each page is read once; its text blocks are kept for the second walk, the
            # rest of what the page holds (glyphs, images) is not.
            page_blocks = [_text_blocks(page) for page in document]
            words_the_document_writes = frozenset(
                word for blocks in page_blocks for word in _written_words(blocks)
            )
            for page_index, (page, text_blocks) in enumerate(
                zip(document, page_blocks, strict=True), start=1
            ):
                page_units, has_text = self._extract_page(
                    page=page,
                    page_index=page_index,
                    text_blocks=text_blocks,
                    words_the_document_writes=words_the_document_writes,
                )
                units.extend(page_units)
                native_text_pages += int(has_text)

        scan_only = native_text_pages == 0
        if scan_only:
            warnings.append(
                WarningRecord(
                    id=stable_id(content_hash, "warning:scan-only"),
                    document_id=document_id,
                    code="pdf_scan_only",
                    message=(
                        "PDF contains no authoritative native text; no OCR or visual "
                        "fallback was applied"
                    ),
                    severity=Severity.WARNING,
                    locator=document_locator,
                )
            )

        nodes, edges, unit_evidence = self._build_graph(
            source=source,
            content_hash=content_hash,
            document_id=document_id,
            document_node=document_node,
            units=units,
        )
        evidence.extend(unit_evidence)
        record = DocumentRecord(
            id=document_id,
            content_hash=content_hash,
            file_name=source.name,
            format=DocumentFormat.PDF,
            source_path=source,
            size_bytes=source.stat().st_size,
            metadata={
                "native_page_count": page_count,
                "native_text_page_count": native_text_pages,
                "scan_only": scan_only,
            },
        )
        return ExtractedDocument(
            document=record,
            nodes=tuple(nodes),
            edges=tuple(edges),
            evidence=tuple(evidence),
            warnings=tuple(warnings),
        ).validate_canonical_semantic_graph()

    def _extract_page(
        self,
        *,
        page: pymupdf.Page,
        page_index: int,
        text_blocks: list[dict[str, Any]],
        words_the_document_writes: frozenset[str],
    ) -> tuple[list[_NativeUnit], bool]:
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        tables = self._tables_of(page, page_width, page_height)
        table_bboxes = [self._bbox(table.bbox) for table in tables]
        units: list[_NativeUnit] = []

        for table_occurrence, table in enumerate(tables):
            values = table.extract()
            rows = [
                ["" if value is None else encodable(str(value)).strip() for value in row]
                for row in values
            ]
            text = "\n".join(" | ".join(row) for row in rows).strip()
            if not text:
                continue
            bbox = self._bbox(table.bbox)
            units.append(
                _NativeUnit(
                    page=page_index,
                    occurrence=table_occurrence,
                    type=NodeType.TABLE,
                    label=self._short_label(text, f"Table {table_occurrence + 1}"),
                    text=text,
                    bbox=bbox,
                    metadata={
                        "native_kind": "table",
                        "native_occurrence": table_occurrence,
                        "normalized_bbox": self._normalized_bbox(
                            bbox, page_width, page_height
                        ),
                        "row_count": len(rows),
                        "column_count": max((len(row) for row in rows), default=0),
                    },
                    evidence_kind=EvidenceKind.SOURCE_VALUES,
                    evidence_data=cast(JsonValue, {"rows": rows}),
                )
            )

        for block_occurrence, block in enumerate(text_blocks):
            # A native table owns its values. A block whose lines run across the page picks
            # up the cells of a table standing beside it, so the block keeps only the lines
            # that fall outside every table -- otherwise the paragraph carried the table's
            # words and, covering the table, was read as part of it.
            block = self._outside_tables(block, table_bboxes)
            text = self._block_text(block, words_the_document_writes)
            if not text:
                continue
            bbox = self._bbox(block["bbox"])
            if any(
                self._intersection_ratio(bbox, table_bbox) >= 0.6
                for table_bbox in table_bboxes
            ):
                continue
            max_size = max(
                (
                    float(span.get("size", 0.0))
                    for line in block.get("lines", ())
                    for span in line.get("spans", ())
                    if str(span.get("text", "")).strip()
                ),
                default=0.0,
            )
            # A block is text. Its size is recorded as the fact it is; which block leads
            # which is the outline pass's question, and a guess made here from a font size
            # used to build a whole tree of sections nobody could check.
            units.append(
                _NativeUnit(
                    page=page_index,
                    occurrence=int(block.get("number", block_occurrence)),
                    type=NodeType.TEXT_CHUNK,
                    label=self._short_label(text, f"Text block {block_occurrence + 1}"),
                    text=text,
                    bbox=bbox,
                    metadata={
                        "font_size": max_size,
                        "native_kind": "text_block",
                        "normalized_bbox": self._normalized_bbox(
                            bbox, page_width, page_height
                        ),
                        "native_occurrence": int(block.get("number", block_occurrence)),
                    },
                    evidence_kind=EvidenceKind.SOURCE_TEXT,
                )
            )

        units.extend(
            self._extract_images(page=page, page_index=page_index)
        )
        units.sort(
            key=lambda item: (
                item.bbox[1],
                item.bbox[0],
                self._type_order(item.type),
                item.occurrence,
            )
        )
        return units, any(unit.text for unit in units if unit.type is not NodeType.IMAGE)

    def _extract_images(
        self,
        *,
        page: pymupdf.Page,
        page_index: int,
    ) -> list[_NativeUnit]:
        result: list[_NativeUnit] = []
        image_infos = page.get_image_info(xrefs=True)
        for occurrence, info in enumerate(image_infos):
            # Reported in the unturned page, like its text (`page_text`).
            bbox = self._bbox(pymupdf.Rect(info["bbox"]) * page.rotation_matrix)  # type: ignore[no-untyped-call]
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            content, mime_type, suffix = self._image_payload(page, info, bbox)
            asset_hash = (
                self._asset_store.put(content, suffix=suffix)
                if self._asset_store
                else None
            )
            width = int(info.get("width", 0)) or None
            height = int(info.get("height", 0)) or None
            data: dict[str, JsonValue] = {
                "native_kind": "image",
                "native_occurrence": occurrence,
                "xref": int(info.get("xref", 0)),
                "width": width,
                "height": height,
                "size_bytes": len(content),
            }
            if asset_hash is None:
                # The graph remains grounded when used without an asset store, while the
                # runtime always persists the exact bytes and asset hash.
                data["content_hash"] = hashlib.sha256(content).hexdigest()
            result.append(
                _NativeUnit(
                    page=page_index,
                    occurrence=occurrence,
                    type=NodeType.IMAGE,
                    label=f"Image {occurrence + 1}",
                    text=None,
                    bbox=bbox,
                    metadata={
                        "native_kind": "image",
                        "native_occurrence": occurrence,
                        "normalized_bbox": self._normalized_bbox(
                            bbox, float(page.rect.width), float(page.rect.height)
                        ),
                        "width": width,
                        "height": height,
                    },
                    evidence_kind=EvidenceKind.SOURCE_MEDIA,
                    evidence_data=data,
                    asset_hash=asset_hash,
                    mime_type=mime_type,
                )
            )
        return result

    @staticmethod
    def _image_payload(
        page: pymupdf.Page,
        info: dict[str, Any],
        bbox: tuple[float, float, float, float],
    ) -> tuple[bytes, str, str]:
        xref = int(info.get("xref", 0))
        if xref > 0:
            extracted = page.parent.extract_image(xref)
            content = extracted.get("image")
            extension = str(extracted.get("ext", "")).lower()
            if isinstance(content, bytes) and content:
                media = PDFExtractor._image_media_type(extension)
                if media is not None:
                    mime_type, suffix = media
                    return content, mime_type, suffix
        # Inline/masked images and unusual PDF encodings may not have an extractable
        # xref. Rasterizing the exact occurrence bbox is deterministic and preserves it.
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(2, 2),  # type: ignore[no-untyped-call]
            colorspace=pymupdf.csRGB,
            alpha=False,
            clip=pymupdf.Rect(*bbox),  # type: ignore[no-untyped-call]
        )
        return (
            pixmap.tobytes("png"),  # type: ignore[no-untyped-call]
            "image/png",
            ".png",
        )

    @staticmethod
    def _image_media_type(extension: str) -> tuple[str, str] | None:
        normalized = {"jpeg": "jpg", "jpe": "jpg"}.get(extension, extension)
        if normalized == "jpg":
            return "image/jpeg", ".jpg"
        if normalized in {"png", "gif", "bmp", "tiff", "webp"}:
            return (
                f"image/{normalized}",
                f".{normalized}",
            )
        # Unknown PDF image encodings must be rasterized rather than assigning an
        # incorrect MIME type to their original bytes.
        return None

    def _build_graph(
        self,
        *,
        source: Path,
        content_hash: str,
        document_id: str,
        document_node: GraphNode,
        units: list[_NativeUnit],
    ) -> tuple[list[GraphNode], list[GraphEdge], list[EvidenceRecord]]:
        nodes = [document_node]
        edges: list[GraphEdge] = []
        evidence: list[EvidenceRecord] = []

        for reading_order, unit in enumerate(units, start=1):
            kind = {
                NodeType.TEXT_CHUNK: "pdf_text_block",
                NodeType.TABLE: "pdf_table",
                NodeType.IMAGE: "pdf_image",
            }[unit.type]
            node_id = stable_id(
                content_hash,
                canonical_locator(kind, {"page": unit.page, "occurrence": unit.occurrence}),
            )
            locator = self._locator(source, unit, unit.label)
            metadata = {**unit.metadata, "reading_order": reading_order}
            nodes.append(
                GraphNode(
                    id=node_id,
                    document_id=document_id,
                    type=unit.type,
                    label=unit.label,
                    locator=locator,
                    text=unit.text,
                    metadata=metadata,
                )
            )
            edges.append(
                native_edge(
                    content_hash, document_id, document_id, node_id, EdgeType.CONTAINS,
                    locator=locator,
                )
            )
            evidence.append(
                EvidenceRecord(
                    id=stable_id(
                        content_hash,
                        f"evidence:{kind}:page={unit.page}:occ={unit.occurrence}",
                    ),
                    document_id=document_id,
                    node_id=node_id,
                    kind=unit.evidence_kind,
                    locator=locator,
                    text=unit.text,
                    data=unit.evidence_data,
                    asset_hash=unit.asset_hash,
                    mime_type=unit.mime_type,
                )
            )

        return nodes, edges, evidence

    @staticmethod
    def _locator(source: Path, unit: _NativeUnit, label: str) -> SourceLocator:
        return SourceLocator(
            file_name=source.name,
            label=label,
            page=unit.page,
            bbox=unit.bbox,
            metadata={
                "native_kind": str(unit.metadata["native_kind"]),
                "native_occurrence": unit.occurrence,
            },
        )

    @classmethod
    def _outside_tables(
        cls, block: dict[str, Any], table_bboxes: list[tuple[float, float, float, float]]
    ) -> dict[str, Any]:
        """The block with every line that sits inside a table taken out.

        pymupdf groups a page's text into blocks by bands, so a paragraph in the left
        column and the cells of a table to its right arrive as one block. Left whole, the
        paragraph holds the table's values and its box covers the table.
        """
        if not table_bboxes:
            return block
        kept = []
        for line in block.get("lines", ()):
            box = cls._bbox(line["bbox"])
            if any(cls._intersection_ratio(box, table) >= 0.5 for table in table_bboxes):
                continue
            kept.append(line)
        if len(kept) == len(block.get("lines", ())):
            return block
        if not kept:
            return {**block, "lines": (), "bbox": block["bbox"]}
        boxes = [cls._bbox(line["bbox"]) for line in kept]
        return {
            **block,
            "lines": kept,
            "bbox": (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ),
        }

    @staticmethod
    def _block_text(
        block: dict[str, Any], words_the_document_writes: frozenset[str] = frozenset()
    ) -> str:
        """A block's words, as the page reads rather than as the extractor grouped them.

        pymupdf reports a "line" per run it grouped, so a justified line whose words were
        stretched apart arrives one word to a line. Those share a baseline and are put back
        together with the space that was printed between them. Only where the baseline
        changes has the page actually wrapped, and there a word the printer split at the
        margin is made whole again.
        """
        return join_printed_lines(
            [text for text, _ in printed_block_lines(block)], words_the_document_writes
        )

    def _tables_of(
        self, page: Any, page_width: float, page_height: float, clip: Any = None, depth: int = 0
    ) -> list[Any]:
        """The tables on a page, looking inside a border for the tables it encloses.

        A page drawn inside a frame parses as one table, and the finder stops there: the
        real tables inside it are never returned. So a frame is opened rather than
        reported, and what it holds is searched again a little way inside its own edges.
        """
        found: list[Any] = []
        for table in page.find_tables(clip=clip).tables:
            if self._is_highlight(page, table):
                continue  # its words stay in the paragraph they are part of
            if not self._is_layout_frame(table, page_width, page_height):
                found.append(table)
                continue
            if depth >= FRAME_DEPTH:
                continue
            x0, y0, x1, y1 = table.bbox
            inside = pymupdf.Rect(  # type: ignore[no-untyped-call]
                x0 + FRAME_INSET, y0 + FRAME_INSET, x1 - FRAME_INSET, y1 - FRAME_INSET
            )
            if inside.is_empty or inside.width <= 0 or inside.height <= 0:
                continue
            found.extend(
                self._tables_of(page, page_width, page_height, inside, depth + 1)
            )
        return found

    @staticmethod
    def _is_highlight(page: Any, table: Any) -> bool:
        """Whether a detected table is a highlight: fills painted behind a line of text.

        A highlight is drawn as filled rectangles, and where two meet -- a phrase marked in
        two runs -- the finder reads their shared edge as a column rule and returns a
        one-row table of the phrase. What gives it away is that the row is one line of
        text tall. A table shaded the same way has rows, and a boxed callout is taller than
        its text.
        """
        if table.row_count != 1:
            return False
        sizes = [
            float(span.get("size", 0.0))
            for block in page_text(page, clip=table.bbox).get("blocks", ())
            for line in block.get("lines", ())
            for span in line.get("spans", ())
            if str(span.get("text", "")).strip()
        ]
        if not sizes:
            return False
        _, y0, _, y1 = PDFExtractor._bbox(table.bbox)
        return y1 - y0 <= HIGHLIGHT_LINE_HEIGHTS * max(sizes)

    @staticmethod
    def _is_layout_frame(table: Any, page_width: float, page_height: float) -> bool:
        """Whether a detected table is really the border a report is drawn inside.

        A page drawn inside a frame parses as a table, and every paragraph on the page
        becomes one of its cells: the whole document collapses into a single table node
        holding the page's text, with nothing left to read separately.

        A real table is mostly full and each cell is a value. A frame is mostly empty and
        one of its cells is a paragraph -- that is what gives it away, not its size: a
        genuine table can fill a page, and a frame can sit well inside the margins.
        """
        x0, y0, x1, y1 = table.bbox
        page_area = max(1.0, page_width * page_height)
        if (x1 - x0) * (y1 - y0) / page_area < FRAME_PAGE_SHARE:
            return False
        cells = [
            "" if value is None else str(value).strip()
            for row in table.extract()
            for value in row
        ]
        if not cells:
            return False
        filled = [cell for cell in cells if cell]
        return (
            len(filled) / len(cells) < FRAME_FILL_RATIO
            and max((len(cell) for cell in filled), default=0) > FRAME_CELL_CHARS
        )

    @staticmethod
    def _bbox(value: Iterable[float]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = (float(item) for item in value)
        return x0, y0, x1, y1

    @staticmethod
    def _normalized_bbox(
        bbox: tuple[float, float, float, float],
        page_width: float,
        page_height: float,
    ) -> list[JsonValue]:
        if page_width <= 0 or page_height <= 0:
            raise ValueError("PDF page dimensions must be positive")
        return [
            bbox[0] / page_width,
            bbox[1] / page_height,
            bbox[2] / page_width,
            bbox[3] / page_height,
        ]

    @staticmethod
    def _intersection_ratio(
        first: tuple[float, float, float, float],
        second: tuple[float, float, float, float],
    ) -> float:
        width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
        area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
        return width * height / area if area else 0.0

    @staticmethod
    def _short_label(text: str, fallback: str) -> str:
        first_line = " ".join(text.splitlines()[0].split()) if text.strip() else ""
        return first_line[:160] or fallback

    @staticmethod
    def _type_order(node_type: NodeType) -> int:
        return {
            NodeType.TEXT_CHUNK: 1,
            NodeType.TABLE: 2,
            NodeType.IMAGE: 3,
        }[node_type]


def printed_block_lines(block: dict[str, Any]) -> list[tuple[str, _Box]]:
    """A block's lines as the page printed them, with what was drawn on each.

    pymupdf reports a "line" per run it grouped, so a justified line whose words were
    stretched apart arrives one word to a line. Those share a baseline and are one printed
    line: what a reader sees, and what anything asking about this page must be shown.
    """
    printed: list[tuple[str, _Box]] = []
    baseline: float | None = None
    for line in block.get("lines", ()):
        text = "".join(str(span.get("text", "")) for span in line.get("spans", ())).strip()
        if not text:
            continue
        box = line["bbox"]
        top, bottom = float(box[1]), float(box[3])
        drawn: _Box = (float(box[0]), top, float(box[2]), bottom)
        if baseline is not None and top <= baseline <= bottom:
            was_text, was_box = printed[-1]
            printed[-1] = (
                f"{was_text} {text}",
                (
                    min(was_box[0], drawn[0]),
                    min(was_box[1], drawn[1]),
                    max(was_box[2], drawn[2]),
                    max(was_box[3], drawn[3]),
                ),
            )
            continue
        printed.append((text, drawn))
        baseline = (top + bottom) / 2
    return printed


_Box = tuple[float, float, float, float]


_LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\ufb05": "st", "\ufb06": "st",
}
"""What the font drew as one glyph, written as the letters the document says. A reader
searching for "figure" is not searching for U+FB01."""

_BROKEN_WORD = re.compile(r"(?<=[^\W\d_])[-\u00ad\u2010\u2011]$")
"""A hyphen at the end of a line, following a letter. Whose it is, the document says."""

_LAST_WORD = re.compile(r"[^\W\d_]+$")
_FIRST_WORD = re.compile(r"[^\W\d_]+")
_A_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_HANGUL_END = re.compile(r"[\uac00-\ud7a3]$")
_HANGUL_START = re.compile(r"^[\uac00-\ud7a3]")
_HANGUL_RUN_END = re.compile(r"[\uac00-\ud7a3]+$")
_HANGUL_RUN_START = re.compile(r"^[\uac00-\ud7a3]+")


def _text_blocks(page: pymupdf.Page) -> list[dict[str, Any]]:
    """The page's text blocks, in reading order (`page_text`, sorted)."""
    return [b for b in page_text(page, sort=True).get("blocks", ()) if b.get("type") == 0]


def _written_words(text_blocks: Iterable[dict[str, Any]]) -> Iterator[str]:
    """Every word the document writes where no margin could have changed it.

    A line that ends at the margin says nothing about what it did to the word there:
    "real-" then "world" reads exactly like "evalu-" then "ation", and Korean, which
    hyphenates nothing and has no case, gives even less. The document itself answers both
    -- a paper that writes "real-world" mid-line means it at a line end too, and one that
    writes a Korean word whole somewhere means it whole where the margin split it.
    """
    for block in text_blocks:
        for line in block.get("lines", ()):
            text = "".join(str(span.get("text", "")) for span in line.get("spans", ())).strip()
            for match in _A_WORD.finditer(text):
                yield match.group().casefold()


def join_printed_lines(
    texts: Sequence[str], words_the_document_writes: frozenset[str] = frozenset()
) -> str:
    """Printed lines as running text: what the margin broke, made whole again.

    The same joining the extractor does inside a block, offered to whatever decides that a
    block was really several units -- a unit cut out of a block is still printed lines, and
    a word the margin split does not care where the boundary fell.
    """
    return _ligatures(_unwrapped(list(texts), words_the_document_writes))


def _ligatures(text: str) -> str:
    for glyph, letters in _LIGATURES.items():
        text = text.replace(glyph, letters)
    return text


def _unwrapped(printed: Sequence[str], words_the_document_writes: frozenset[str]) -> str:
    """The printed lines as running text, with words the margin broke made whole.

    A trailing hyphen is the printer's only when a lowercase word follows it; before a
    capital or a digit it is the writer's own (ISO-Standard, 2024-25) and stays, as does
    the line break's space. Korean wraps with no hyphen and no case, so nothing here fires
    on it -- the lines join with a space, which is what the page shows between them.
    """
    out = ""
    for line in printed:
        if not out:
            out = line
            continue
        if _BROKEN_WORD.search(out) and line[:1].islower():
            head = _LAST_WORD.search(out[:-1])
            tail = _FIRST_WORD.match(line)
            compound = f"{head.group()}-{tail.group()}".casefold() if head and tail else ""
            # The document says whose hyphen this is: one it writes elsewhere is the
            # writer's and stays, the rest are the margin's and go.
            out = out + line if compound in words_the_document_writes else out[:-1] + line
        elif _HANGUL_END.search(out) and _HANGUL_START.match(line):
            # Korean wraps at a space most of the time and mid-word the rest, leaving no
            # mark either way. A word the document writes whole is one the margin split.
            head = _HANGUL_RUN_END.search(out)
            tail = _HANGUL_RUN_START.match(line)
            whole = f"{head.group()}{tail.group()}" if head and tail else ""
            out = out + line if whole in words_the_document_writes else f"{out} {line}"
        else:
            out = f"{out} {line}"
    return out.strip()


def printed_pieces(
    source: Path, nodes: Iterable[Any]
) -> tuple[dict[str, tuple[str, ...]], Callable[[Sequence[str]], str]]:
    """Every text block in the pieces it was printed in, and how to make them whole again.

    What a PDF hands over is ink grouped by where it fell, which is neither a line nor a
    paragraph. Whoever decides which pieces are one unit needs both halves of this: the
    pieces to decide about, and the document's own vocabulary to join them without
    inventing a word the margin broke.

    The block a node came from is named by its page and the number the parser gave it, so
    the lines can be read again without the extractor having to carry them on the node.
    Both halves come from one reading of each page.
    """
    wanted = {
        (node.locator.page, node.metadata.get("native_occurrence")): node.id
        for node in nodes
        if node.metadata.get("native_kind") == "text_block"
    }
    pieces: dict[str, tuple[str, ...]] = {}
    written: set[str] = set()
    with pymupdf.open(source) as document:  # type: ignore[no-untyped-call]
        for page_index, page in enumerate(document, start=1):
            text_blocks = _text_blocks(page)
            written.update(_written_words(text_blocks))
            for occurrence, block in enumerate(text_blocks):
                node_id = wanted.get((page_index, int(block.get("number", occurrence))))
                if node_id is None:
                    continue
                lines = printed_block_lines(block)
                if lines:
                    pieces[node_id] = tuple(text for text, _ in lines)
    words = frozenset(written)
    return pieces, lambda texts: join_printed_lines(texts, words)

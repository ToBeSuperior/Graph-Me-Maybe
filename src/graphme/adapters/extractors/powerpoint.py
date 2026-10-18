from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

from graphme.domain.enums import DocumentFormat, EdgeType, EvidenceKind, NodeType
from graphme.domain.ids import canonical_locator, sha256_bytes, sha256_file, stable_id
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    JsonValue,
    SourceLocator,
)
from graphme.ports.storage import AssetStore

_TITLE_PLACEHOLDERS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}


_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_BREAK = f"{{{_DRAWING_NS}}}br"
_FIELD = f"{{{_DRAWING_NS}}}fld"
def _joined_shape_ids(shape: Any) -> list[int]:
    """The shapes a connector states it joins, in start-then-end order.

    A connector's own rectangle spans the diagonal between the two shapes and brushes
    whatever else lies in that span, so the drawn box says nothing reliable about what
    the arrow connects. The file states it.
    """
    if shape.shape_type != MSO_SHAPE_TYPE.LINE:
        return []  # a group holding arrows is not itself an arrow
    properties = shape._element.find(f".//{{{_PRESENTATION_NS}}}cNvCxnSpPr")
    if properties is None:
        return []
    joins = []
    for tag in ("stCxn", "endCxn"):
        endpoint = properties.find(f"{{{_DRAWING_NS}}}{tag}")
        if endpoint is None:
            continue
        value = endpoint.get("id")
        if value is not None and value.isdigit():
            joins.append(int(value))
    return joins


DRAWN_SHAPES = {
    MSO_SHAPE_TYPE.AUTO_SHAPE,
    MSO_SHAPE_TYPE.FREEFORM,
    MSO_SHAPE_TYPE.LINE,
}
"""Shapes a deck draws with. Each is a figure when it carries no words of its own."""


@dataclass(frozen=True, slots=True)
class _Placed:
    """A shape and where it sits on the slide, after any groups above it are undone."""

    shape: Any
    left: int
    top: int
    width: int
    height: int
    from_layout: bool
    path: tuple[int, ...]
    """Shape ids from the outermost group down, so a child of a group has its own name."""


_IDENTITY = (1.0, 1.0, 0.0, 0.0)


def _group_transform(group: Any) -> tuple[float, float, float, float]:
    """How a group maps its children onto the slide: (scale x, scale y, shift x, shift y).
    A group that states no space of its own measures its children where it stands.

    A group gives its children a coordinate space of their own and then states where that
    space lands. Read raw, a child's position is measured against the wrong origin.
    """
    # The group's own `grpSpPr`, never a descendant: a search down the tree finds the
    # first child shape's rectangle and reads one shape's placement as the mapping for
    # all of them. The same shape of mistake once made a group inherit a child's
    # connector endpoints.
    properties = next(
        (child for child in group._element if child.tag.endswith("}grpSpPr")), None
    )
    xfrm = None if properties is None else properties.find(f"{{{_DRAWING_NS}}}xfrm")
    if xfrm is None:
        return _IDENTITY
    parts = {
        tag: xfrm.find(f"{{{_DRAWING_NS}}}{tag}")
        for tag in ("off", "ext", "chOff", "chExt")
    }
    if any(part is None for part in parts.values()):
        return _IDENTITY
    child_width = int(parts["chExt"].get("cx", 0))
    child_height = int(parts["chExt"].get("cy", 0))
    if child_width <= 0 or child_height <= 0:
        return _IDENTITY
    scale_x = int(parts["ext"].get("cx", 0)) / child_width
    scale_y = int(parts["ext"].get("cy", 0)) / child_height
    shift_x = int(parts["off"].get("x", 0)) - int(parts["chOff"].get("x", 0)) * scale_x
    shift_y = int(parts["off"].get("y", 0)) - int(parts["chOff"].get("y", 0)) * scale_y
    return scale_x, scale_y, shift_x, shift_y


def flatten_shapes(
    shapes: Iterable[Any],
    *,
    from_layout: bool = False,
    transform: tuple[float, float, float, float] = _IDENTITY,
    path: tuple[int, ...] = (),
) -> list[_Placed]:
    """Every drawn shape, with groups opened and their children put back on the slide.

    The group itself is kept: a flow chart is one thing to look at, and the merge lets it
    own the boxes and arrows it is drawn from. Its children are kept too, because the
    words in those boxes are the only place that content exists.
    """
    scale_x, scale_y, shift_x, shift_y = transform
    placed: list[_Placed] = []
    for shape in shapes:
        if shape.left is None or shape.top is None:
            continue
        left = int(shape.left * scale_x + shift_x)
        top = int(shape.top * scale_y + shift_y)
        width = int((shape.width or 0) * scale_x)
        height = int((shape.height or 0) * scale_y)
        here = (*path, int(shape.shape_id))
        placed.append(_Placed(shape, left, top, width, height, from_layout, here))
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            inner = _group_transform(shape)
            placed.extend(
                flatten_shapes(
                    shape.shapes,
                    from_layout=from_layout,
                    transform=(
                        inner[0] * scale_x,
                        inner[1] * scale_y,
                        inner[2] * scale_x + shift_x,
                        inner[3] * scale_y + shift_y,
                    ),
                    path=here,
                )
            )
    return placed


def _layout_shapes(slide: Any, width: int, height: int) -> list[Any]:
    """Layout shapes the rendered page actually draws.

    A deck often keeps its title, its footer and its logos on the layout rather than on
    the slide, and the renderer draws them, so a graph built from `slide.shapes` alone
    describes a different page from the one the model is shown.

    Two are left out. A layout placeholder is a prompt the slide fills in -- taking it
    would duplicate the slide's own text with "제목을 입력하십시오". And a template parks its
    designer notes outside the slide, where nothing is drawn; anything that does not
    overlap the slide rectangle is one of those.
    """
    kept: list[Any] = []
    for shape in slide.slide_layout.shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.PLACEHOLDER or shape.is_placeholder:
            continue
        left, top = shape.left, shape.top
        if left is None or top is None:
            continue
        right = left + (shape.width or 0)
        bottom = top + (shape.height or 0)
        if right <= 0 or bottom <= 0 or left >= width or top >= height:
            continue  # parked off-canvas: a note to the designer, not content
        kept.append(shape)
    return kept


class PowerPointExtractor:
    """Extract the authoritative native structure and content of a PPTX deck."""

    format = DocumentFormat.PPTX
    version = "2"
    """2: a line break parts its words, a group stating no child space keeps its
    children, and a chart python-pptx cannot read costs only its own series."""

    def __init__(self, *, asset_store: AssetStore | None = None) -> None:
        self._asset_store = asset_store

    def extract(self, source: Path) -> ExtractedDocument:
        content_hash = sha256_file(source)
        presentation = Presentation(str(source))
        slide_width = int(presentation.slide_width or 1)
        slide_height = int(presentation.slide_height or 1)
        document_id = self._node_id(content_hash, "document", file=source.name)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        evidence: list[EvidenceRecord] = []

        document_locator = SourceLocator(file_name=source.name, label=source.name)
        nodes.append(
            GraphNode(
                id=document_id,
                document_id=document_id,
                type=NodeType.DOCUMENT,
                label=source.name,
                locator=document_locator,
                content_hash=content_hash,
            )
        )
        self._add_evidence(
            evidence,
            content_hash=content_hash,
            document_id=document_id,
            node_id=document_id,
            key="document",
            kind=EvidenceKind.SOURCE_METADATA,
            locator=document_locator,
            text=f"presentation {source.name} with {len(presentation.slides)} slides",
        )

        slide_ids: list[str] = []
        for slide_number, slide in enumerate(presentation.slides, start=1):
            title = self._slide_title(slide) or f"Slide {slide_number}"
            slide_id = self._node_id(content_hash, "slide", slide=str(slide_number))
            slide_ids.append(slide_id)
            slide_locator = SourceLocator(
                file_name=source.name,
                label=title,
                slide=slide_number,
            )
            nodes.append(
                GraphNode(
                    id=slide_id,
                    document_id=document_id,
                    type=NodeType.SLIDE,
                    label=title,
                    locator=slide_locator,
                    text=title if title != f"Slide {slide_number}" else None,
                    metadata={
                        "slide_number": slide_number,
                        "width_emu": slide_width,
                        "height_emu": slide_height,
                    },
                )
            )
            self._add_evidence(
                evidence,
                content_hash=content_hash,
                document_id=document_id,
                node_id=slide_id,
                key=f"slide:{slide_number}",
                kind=EvidenceKind.SOURCE_METADATA,
                locator=slide_locator,
                text=f"slide {slide_number}: {title}",
            )
            edges.append(
                self._edge(content_hash, document_id, document_id, slide_id, EdgeType.CONTAINS)
            )

            child_ids: list[str] = []
            drawn = flatten_shapes(slide.shapes)
            drawn += flatten_shapes(
                _layout_shapes(slide, slide_width, slide_height), from_layout=True
            )
            ordered_shapes = sorted(
                enumerate(drawn), key=lambda item: (item[1].top, item[1].left, item[0])
            )
            for source_order, (_, placed) in enumerate(ordered_shapes, start=1):
                shape = placed.shape
                child_id = self._extract_shape(
                    source=source,
                    content_hash=content_hash,
                    document_id=document_id,
                    slide_number=slide_number,
                    shape=shape,
                    placed=placed,
                    source_order=source_order,
                    slide_width=slide_width,
                    slide_height=slide_height,
                    nodes=nodes,
                    evidence=evidence,
                )
                if child_id is not None:
                    child_ids.append(child_id)
                    edges.append(
                        self._edge(
                            content_hash,
                            document_id,
                            slide_id,
                            child_id,
                            EdgeType.CONTAINS,
                        )
                    )
        # Slides are numbered, so one follows another and the file says so. What is placed
        # on a slide is not a sequence; where it reads is the outline pass's question.
        self._add_next_chain(content_hash, document_id, slide_ids, edges)
        return ExtractedDocument(
            document=DocumentRecord(
                id=document_id,
                content_hash=content_hash,
                file_name=source.name,
                format=DocumentFormat.PPTX,
                source_path=source,
                size_bytes=source.stat().st_size,
                metadata={"slide_count": len(presentation.slides)},
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
            evidence=tuple(evidence),
        ).validate_canonical_semantic_graph()

    def _extract_shape(
        self,
        *,
        source: Path,
        content_hash: str,
        document_id: str,
        slide_number: int,
        shape: Any,
        source_order: int,
        slide_width: int,
        slide_height: int,
        nodes: list[GraphNode],
        evidence: list[EvidenceRecord],
        placed: _Placed,
    ) -> str | None:
        geometry = self._geometry(placed, source_order, slide_width, slide_height)
        # A layout numbers its shapes independently of the slide, and a group numbers its
        # children independently again, so the origin and the path are part of the key.
        trail = ":".join(str(part) for part in placed.path)
        shape_key = f"layout:{trail}" if placed.from_layout else trail
        label = (shape.name or f"Shape {shape_key}").strip()
        locator = SourceLocator(
            file_name=source.name,
            label=label,
            slide=slide_number,
            bbox=geometry["normalized_bbox"],
        )

        if getattr(shape, "has_table", False):
            rows = self._table_rows(shape.table)
            text = "\n".join("\t".join(row) for row in rows)
            node_id = self._node_id(content_hash, "table", slide=str(slide_number), shape=shape_key)
            nodes.append(
                GraphNode(
                    id=node_id,
                    document_id=document_id,
                    type=NodeType.TABLE,
                    label=label,
                    locator=locator,
                    text=text,
                    metadata={
                        **geometry,
                        "rows": rows,
                        "row_count": len(rows),
                        "column_count": len(rows[0]) if rows else 0,
                    },
                )
            )
            self._add_evidence(
                evidence,
                content_hash=content_hash,
                document_id=document_id,
                node_id=node_id,
                key=f"table:{slide_number}:{shape_key}",
                kind=EvidenceKind.SOURCE_VALUES,
                locator=locator,
                text=text,
            )
            return node_id

        if getattr(shape, "has_chart", False):
            chart = shape.chart
            title = self._chart_title(chart) or label
            node_id = self._node_id(content_hash, "chart", slide=str(slide_number), shape=shape_key)
            metadata = {**geometry, **self._chart_metadata(chart)}
            chart_locator = locator.model_copy(update={"label": title})
            nodes.append(
                GraphNode(
                    id=node_id,
                    document_id=document_id,
                    type=NodeType.CHART,
                    label=title,
                    locator=chart_locator,
                    metadata=metadata,
                )
            )
            self._add_evidence(
                evidence,
                content_hash=content_hash,
                document_id=document_id,
                node_id=node_id,
                key=f"chart:{slide_number}:{shape_key}",
                kind=EvidenceKind.SOURCE_METADATA,
                locator=chart_locator,
                data=metadata,
            )
            return node_id

        if shape.shape_type == MSO_SHAPE_TYPE.GROUP or (
            not self._shape_text(shape) and shape.shape_type in DRAWN_SHAPES
        ):
            # A group, a box, an arrow, a rule: drawn from parts rather than from a file,
            # so there is nothing to store and the rendering is where the picture comes
            # from. A deck draws with these, and they carry the meaning of a slide as much
            # as a photograph does; returning nothing for them left the diagram they form
            # out of the graph. A shape that carries words stays those words.
            node_id = self._node_id(
                content_hash, "image", slide=str(slide_number), shape=shape_key
            )
            joins = _joined_shape_ids(shape)
            nodes.append(
                GraphNode(
                    id=node_id,
                    document_id=document_id,
                    type=NodeType.IMAGE,
                    label=label,
                    locator=locator,
                    metadata={
                        **geometry,
                        "drawn_from_parts": True,
                        **({"joins": joins} if joins else {}),
                    },
                )
            )
            self._add_evidence(
                evidence,
                content_hash=content_hash,
                document_id=document_id,
                node_id=node_id,
                key=f"image:{slide_number}:{shape_key}",
                kind=EvidenceKind.SOURCE_MEDIA,
                locator=locator,
                data={"drawn_from_parts": True},
            )
            return node_id

        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            image = shape.image
            blob = image.blob
            extension = image.ext or "bin"
            media_hash = sha256_bytes(blob)
            asset_hash = (
                self._asset_store.put(blob, suffix=f".{extension}") if self._asset_store else None
            )
            node_id = self._node_id(content_hash, "image", slide=str(slide_number), shape=shape_key)
            image_locator = locator.model_copy(
                update={
                    "relationship_id": self._image_relationship_id(shape),
                    "source_part": str(image.filename),
                }
            )
            nodes.append(
                GraphNode(
                    id=node_id,
                    document_id=document_id,
                    type=NodeType.IMAGE,
                    label=label,
                    locator=image_locator,
                    metadata={**geometry, "filename": image.filename, "media_hash": media_hash},
                )
            )
            self._add_evidence(
                evidence,
                content_hash=content_hash,
                document_id=document_id,
                node_id=node_id,
                key=f"image:{slide_number}:{shape_key}",
                kind=EvidenceKind.SOURCE_MEDIA,
                locator=image_locator,
                asset_hash=asset_hash,
                mime_type=image.content_type if asset_hash else None,
                data={"filename": image.filename, "media_hash": media_hash},
            )
            return node_id

        text = self._shape_text(shape)
        if not text:
            return None
        # A title placeholder is text. Which shape the layout called a title is kept as
        # the fact it is; what the slide reads under is the outline pass's question.
        is_title = self._is_title(shape)
        node_type = NodeType.TEXT_CHUNK
        node_id = self._node_id(
            content_hash, node_type.value, slide=str(slide_number), shape=shape_key
        )
        text_locator = locator.model_copy(update={"label": text.splitlines()[0]})
        nodes.append(
            GraphNode(
                id=node_id,
                document_id=document_id,
                type=node_type,
                label=text.splitlines()[0],
                locator=text_locator,
                text=text,
                metadata={
                    **geometry,
                    "layout_role": "title" if is_title else "body",
                    "placeholder_type": self._placeholder_name(shape),
                },
            )
        )
        self._add_evidence(
            evidence,
            content_hash=content_hash,
            document_id=document_id,
            node_id=node_id,
            key=f"text:{slide_number}:{shape_key}",
            kind=EvidenceKind.SOURCE_TEXT,
            locator=text_locator,
            text=text,
        )
        return node_id

    @staticmethod
    def _shape_text(shape: Any) -> str:
        if not getattr(shape, "has_text_frame", False):
            return ""
        paragraphs = []
        for paragraph in shape.text_frame.paragraphs:
            # A run's words and a line break (`a:br`) between them; a field is left out.
            text = "".join(
                "\n" if child.tag == _BREAK else child.text
                for child in paragraph._p.content_children
                if child.tag != _FIELD
            ).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)

    def _slide_title(self, slide: Any) -> str | None:
        title_shape = slide.shapes.title
        if title_shape is None:
            return None
        return self._shape_text(title_shape) or None

    @staticmethod
    def _is_title(shape: Any) -> bool:
        return bool(
            getattr(shape, "is_placeholder", False)
            and shape.placeholder_format.type in _TITLE_PLACEHOLDERS
        )

    @staticmethod
    def _placeholder_name(shape: Any) -> str | None:
        if not getattr(shape, "is_placeholder", False):
            return None
        value = shape.placeholder_format.type
        return getattr(value, "name", str(value))

    @staticmethod
    def _geometry(
        placed: _Placed, source_order: int, width: int, height: int
    ) -> dict[str, JsonValue]:
        shape = placed.shape
        left, top = placed.left, placed.top
        right, bottom = left + placed.width, top + placed.height
        normalized = (
            max(0.0, min(1.0, left / width)),
            max(0.0, min(1.0, top / height)),
            max(0.0, min(1.0, right / width)),
            max(0.0, min(1.0, bottom / height)),
        )
        return {
            "source_order": source_order,
            "shape_id": int(shape.shape_id),
            "shape_type": getattr(shape.shape_type, "name", str(shape.shape_type)),
            "bbox_emu": [left, top, right, bottom],
            "normalized_bbox": list(normalized),
        }

    @staticmethod
    def _table_rows(table: Any) -> list[list[str]]:
        return [[cell.text.strip() for cell in row.cells] for row in table.rows]

    def _chart_metadata(self, chart: Any) -> dict[str, JsonValue]:
        series: list[JsonValue] = []
        # python-pptx reads a fixed set of plot types; for another (a bar of pie, a stock
        # chart, a surface) it fails on the series, and the chart keeps its frame and title.
        with suppress(AttributeError, KeyError, NotImplementedError, ValueError):
            for index, item in enumerate(chart.series, start=1):
                name = str(item.name) if item.name is not None else f"Series {index}"
                values: list[JsonValue] = [
                    self._json_value(value) for value in item.values
                ]
                series.append({"name": name, "values": values})
        categories: list[JsonValue] = []
        with suppress(AttributeError, IndexError, TypeError, ValueError):
            categories = [
                self._json_value(category.label) for category in chart.plots[0].categories
            ]
        chart_type: JsonValue = None
        with suppress(AttributeError, KeyError, NotImplementedError, ValueError):
            chart_type = getattr(chart.chart_type, "name", str(chart.chart_type))
        return {
            "chart_type": chart_type,
            "title": self._chart_title(chart),
            "series": series,
            "categories": categories,
            "has_legend": bool(chart.has_legend),
        }

    def _chart_title(self, chart: Any) -> str | None:
        if not chart.has_title:
            return None
        return self._shape_text(chart.chart_title) or None

    @staticmethod
    def _json_value(value: Any) -> JsonValue:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _image_relationship_id(shape: Any) -> str | None:
        try:
            return str(shape._pic.blipFill.blip.embed)
        except AttributeError:
            return None

    @staticmethod
    def _node_id(content_hash: str, kind: str, **coordinates: str) -> str:
        return stable_id(content_hash, canonical_locator(kind, coordinates))

    @staticmethod
    def _edge(
        content_hash: str,
        document_id: str,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
    ) -> GraphEdge:
        return GraphEdge(
            id=stable_id(content_hash, f"{edge_type.value.lower()}:{source_id}->{target_id}"),
            document_id=document_id,
            source_id=source_id,
            target_id=target_id,
            type=edge_type,
        )

    def _add_next_chain(
        self,
        content_hash: str,
        document_id: str,
        node_ids: Iterable[str],
        edges: list[GraphEdge],
    ) -> None:
        ordered = list(node_ids)
        for source_id, target_id in zip(ordered, ordered[1:], strict=False):
            edges.append(self._edge(content_hash, document_id, source_id, target_id, EdgeType.NEXT))

    @staticmethod
    def _add_evidence(
        evidence: list[EvidenceRecord],
        *,
        content_hash: str,
        document_id: str,
        node_id: str,
        key: str,
        kind: EvidenceKind,
        locator: SourceLocator,
        text: str | None = None,
        data: JsonValue = None,
        asset_hash: str | None = None,
        mime_type: str | None = None,
    ) -> None:
        evidence.append(
            EvidenceRecord(
                id=stable_id(content_hash, f"evidence:{key}"),
                document_id=document_id,
                node_id=node_id,
                kind=kind,
                locator=locator,
                text=text,
                data=data,
                asset_hash=asset_hash,
                mime_type=mime_type,
            )
        )

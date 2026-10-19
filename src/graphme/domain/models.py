from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypeAliasType

from graphme.domain.enums import (
    DocumentFormat,
    EdgeType,
    EvidenceKind,
    NodeType,
    SemanticUnitKind,
    Severity,
)
from graphme.domain.ids import stable_id

JsonValue = TypeAliasType(
    "JsonValue",
    "str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]",
)
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceLocator(DomainModel):
    file_name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    slide: int | None = Field(default=None, ge=1)
    cell_range: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    relationship_id: str | None = None
    source_part: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is not None and (value[2] < value[0] or value[3] < value[1]):
            raise ValueError("bbox must be ordered as x0, y0, x1, y1")
        return value


class NormalizedBBox(DomainModel):
    x0: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y0: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    x1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_positive_area(self) -> NormalizedBBox:
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("normalized bbox must have strictly positive area")
        return self

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1


class PageSegmentationRequest(DomainModel):
    page_index: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/png"] = "image/png"
    locator: SourceLocator

    @model_validator(mode="after")
    def require_matching_locator_page(self) -> PageSegmentationRequest:
        if self.locator.page != self.page_index:
            raise ValueError("segmentation request locator page must match page index")
        return self


class SegmentedUnit(DomainModel):
    kind: SemanticUnitKind
    reading_order: int = Field(ge=1)
    label: str = Field(min_length=1)
    bbox: NormalizedBBox
    interpreted_text: str | None = None
    structured_data: JsonValue = None
    confidence: Confidence

    @model_validator(mode="after")
    def require_interpretation(self) -> SegmentedUnit:
        # Textual units are their content, so an empty one carries nothing. A picture
        # or chart is located, not transcribed: its label identifies it and a caption
        # is generated later, so absent interpreted_text is the normal case there.
        if self.kind in {SemanticUnitKind.FIGURE, SemanticUnitKind.TABLE}:
            return self
        if self.interpreted_text is None and self.structured_data is None:
            raise ValueError("segmented unit requires an interpretation")
        return self


class PageSegmentationResult(DomainModel):
    page_index: int = Field(ge=1)
    units: tuple[SegmentedUnit, ...]

    @model_validator(mode="after")
    def require_unique_reading_orders(self) -> PageSegmentationResult:
        reading_orders = [unit.reading_order for unit in self.units]
        if len(set(reading_orders)) != len(reading_orders):
            raise ValueError("segmented unit reading orders must be unique")
        return self

    @classmethod
    def from_request(
        cls,
        request: PageSegmentationRequest,
        *,
        units: tuple[SegmentedUnit, ...],
    ) -> PageSegmentationResult:
        return cls(page_index=request.page_index, units=units)

    def validate_for(
        self, request: PageSegmentationRequest
    ) -> PageSegmentationResult:
        if self.page_index != request.page_index:
            raise ValueError("segmentation result page index must match request")
        return self


class HierarchyImageDescriptor(DomainModel):
    """Cryptographic identity for the exact visual input sent to a hierarchy VLM."""

    asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/png"] = "image/png"
    size_bytes: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    document_id: str = Field(min_length=1)
    scope: str = Field(min_length=1, max_length=200)
    page_index: int = Field(ge=1)
    covered_unit_ids: tuple[str, ...] = Field(min_length=1, max_length=500)

    @field_validator("covered_unit_ids")
    @classmethod
    def unique_covered_units(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("covered hierarchy unit IDs must be unique")
        return value


class HierarchyUnit(DomainModel):
    """What a unit is and where it reads, and nothing else.

    A pixel box was carried here once. It came from the cell a thing is anchored to,
    which for overflowing text is a sliver of where the words actually appear, and the
    page itself already shows the model the layout. Two flags were carried too:
    every unit needs a decision, and every unit sits under the sheet, so neither said
    anything that was not true of all of them.
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=200)
    order: int = Field(ge=1)
    node_type: NodeType
    indent: int | None = Field(default=None, ge=0)
    """How far in from the left this unit was drawn, as a level rather than a distance.

    Carried only where the rendering measures where the words actually are. A page of
    running text says nesting with indentation, and the step can be a few points -- small
    enough to be lost in a page image, and exactly what a reader uses."""


class GroupableUnit(DomainModel):
    """One thing on a page that might belong with the things around it.

    A printed line in a PDF, a paragraph in a slide's shape, a picture, a table. What it
    is does not decide the question -- whether it stands alone or with its neighbours is
    what the page is asked.
    """

    id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=4000)
    node_type: NodeType
    page_index: int = Field(ge=1)


class UnitGroupingRequest(DomainModel):
    """One run of a page's pieces, asked which of them are one unit.

    The last few pieces of the ask before come along under `settled`, labelled, so a unit
    broken by a page break or a run's end keeps the same label on both sides.
    """

    request_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1)
    scope: str = Field(min_length=1, max_length=200)
    units: tuple[GroupableUnit, ...] = Field(min_length=1, max_length=400)
    settled: dict[str, str] = Field(default_factory=dict)
    """Labels already given, by unit id: what the window before this one decided."""

    @model_validator(mode="after")
    def validate_units(self) -> UnitGroupingRequest:
        ids = tuple(unit.id for unit in self.units)
        if len(set(ids)) != len(ids):
            raise ValueError("groupable unit IDs must be unique")
        if set(self.settled) - set(ids):
            raise ValueError("a settled label names a unit that is not in this window")
        return self


class UnitLabel(DomainModel):
    """One unit's place: which group it belongs to, and where in it.

    `group` is the group's name and `order` is the unit's position inside it, counting
    from one. Two units share a group when they are one thing printed in pieces.
    """

    unit_id: str = Field(min_length=1)
    group: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    order: int = Field(ge=1)


class UnitGroupingResult(DomainModel):
    """Only the pieces that belong with another piece.

    Most of a page stands alone, so saying so for every piece is a long answer whose one
    missing line makes the whole of it unusable. What is reported is the joining; a piece
    nobody mentions is a unit of its own, which is also what it already was.
    """

    labels: tuple[UnitLabel, ...] = Field(default=(), max_length=400)

    def validate_for(self, request: UnitGroupingRequest) -> UnitGroupingResult:
        asked = {unit.id for unit in request.units}
        seen: set[str] = set()
        places: dict[str, set[int]] = {}
        for label in self.labels:
            if label.unit_id not in asked:
                raise ValueError("a label names a unit that was not asked about")
            if label.unit_id in seen:
                raise ValueError("a unit is placed once")
            seen.add(label.unit_id)
            if label.order in places.setdefault(label.group, set()):
                raise ValueError(f"group {label.group!r} places two units at the same order")
            places[label.group].add(label.order)
        for group, orders in places.items():
            if orders != set(range(1, len(orders) + 1)):
                raise ValueError(f"group {group!r} is missing a place in its order")
        # What the window before settled is settled. A group may carry one of them on --
        # that is how a unit crosses the page break -- but never two, which would say the
        # window before was wrong about both of them.
        by_group: dict[str, set[str]] = {}
        for label in self.labels:
            if (settled := request.settled.get(label.unit_id)) is not None:
                by_group.setdefault(label.group, set()).add(settled)
        for group, carried in by_group.items():
            if len(carried) > 1:
                raise ValueError(
                    f"group {group!r} joins units two settled groups already hold apart"
                )
        return self


class HierarchyScopeRequest(DomainModel):
    """Complete visual scope presented for semantic outline decisions."""

    request_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1)
    scope: str = Field(min_length=1, max_length=200)
    page_index: int = Field(ge=1)
    scope_root_id: str = Field(min_length=1)
    units: tuple[HierarchyUnit, ...] = Field(min_length=1, max_length=500)
    """Every unit needs a decision and every unit may parent another, so the two lists
    that used to say which were the same list twice."""
    outline: tuple[HierarchyUnit, ...] = Field(default=(), max_length=300)
    """The outline earlier pages built, as far as it fits: units already decided, in
    reading order.

    A page is a place the paper ran out, not a place the document changed subject, so the
    first line of a page usually belongs under a heading printed on the page before. What
    comes along is not one chain but the shape read so far -- a heading whose items are
    all here says what kind of thing reads under it, which is how the same section is
    recognised on the page after the one it was printed on. These are not decided again;
    they may be named as a parent."""
    outline_parents: dict[str, str] = Field(default_factory=dict)
    """Who holds whom inside `outline`, child to parent. A unit with no entry stands at
    the top. Every parent named here is itself carried: an outline that points at
    something it does not contain is not an outline."""
    outline_open: tuple[str, ...] = Field(default=(), max_length=50)
    """The units still open where this page begins, outermost first -- the path from the
    document down to where the page before stopped. These are the places this page can
    attach to, and they are the only carried units allowed to be missing children: what
    hangs off them may have been dropped to fit, while everything else in `outline` is
    carried whole or not at all."""
    outline_omitted: dict[str, int] = Field(default_factory=dict)
    """How many of a carried unit's own subtrees were dropped to fit, by its id. Without
    it a section whose earlier items did not fit reads as a section that began here."""
    image: HierarchyImageDescriptor

    @model_validator(mode="after")
    def validate_complete_scope(self) -> HierarchyScopeRequest:
        unit_ids = {unit.id for unit in self.units}
        if len(unit_ids) != len(self.units):
            raise ValueError("hierarchy scope unit IDs must be unique")
        if any(unit.scope != self.scope for unit in self.units):
            raise ValueError("hierarchy scope units must share scope")
        if self.scope_root_id in unit_ids:
            raise ValueError("scope root must not be a visual unit")
        carried_ids = {unit.id for unit in self.outline}
        if len(carried_ids) != len(self.outline):
            raise ValueError("carried outline unit IDs must be unique")
        if carried_ids & unit_ids:
            raise ValueError("a carried unit is decided on its own page, not again here")
        # A parent that is not itself carried leaves the unit under it hanging: the page
        # is shown a child of something it cannot see, which is no context at all.
        if set(self.outline_parents) - carried_ids:
            raise ValueError("a carried parent link must start at a carried unit")
        if set(self.outline_parents.values()) - carried_ids:
            raise ValueError("a carried unit's parent must be carried with it")
        if set(self.outline_open) - carried_ids:
            raise ValueError("an open unit must be carried in the outline")
        if set(self.outline_omitted) - carried_ids:
            raise ValueError("an omitted count must be keyed by a carried unit")

        if self.image.document_id != self.document_id or self.image.scope != self.scope:
            raise ValueError("hierarchy image must match request document and scope")
        # The page has to show every unit being judged, or a decision about one rests
        # on something the model never saw.
        if not unit_ids.issubset(set(self.image.covered_unit_ids)):
            raise ValueError("image must cover every scope unit")
        return self


class HierarchyDecision(DomainModel):
    subject_id: str = Field(min_length=1)
    status: Literal["parent", "root", "unresolved", "drop"]
    """Where the subject reads, or that it should not be in the graph at all.

    `drop` is for what the page carries without saying anything: a page number, a header or
    footer repeated on every page, a template watermark, a rule under a title, a stock
    photograph filling space on a title page. Left in, they are passages a search returns --
    page numbers became a linked document's nearest passage. The pass that reads the page is
    the one that can tell, a picture especially, which is decoration or content by what it
    shows. A wrong call loses what no later pass gets back, which is why the prompt asks to
    drop only what would be noise if a search returned it."""
    parent_id: str | None = Field(default=None, min_length=1)
    confidence: Confidence
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_parent_status(self) -> HierarchyDecision:
        if self.status == "parent" and self.parent_id is None:
            raise ValueError("parent decision requires parent_id")
        if self.status != "parent" and self.parent_id is not None:
            raise ValueError("only parent decisions may contain parent_id")
        return self


class HierarchyFigureGroup(DomainModel):
    """A picture and the units drawn as part of it.

    A drawing's labels are written beside the part they point at, so a label can lie
    almost wholly outside the picture's own rectangle and still be what the picture is
    saying. Measurement cannot tell that from a heading that happens to sit near a photo;
    the page can, and this is what it says: these belong to that figure.
    """

    figure_id: str = Field(min_length=1)
    member_ids: tuple[str, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_members(self) -> HierarchyFigureGroup:
        if self.figure_id in self.member_ids:
            raise ValueError("a figure is not a part of itself")
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("a figure names each part once")
        return self


class HierarchyDerivation(DomainModel):
    """A document's derived outline, and the pages it could not be read on.

    A page the model gave no usable answer for keeps the flat outline it had. That looks
    exactly like a page read as flat, so which pages were left is part of the result.
    """

    decisions: tuple[HierarchyDecision, ...] = ()
    unread_pages: tuple[int, ...] = ()


class HierarchyChoice(DomainModel):
    """Which of two readings of the same page is the one to keep.

    A single pass is the only witness to its own answer. Two independent readings of the
    same rendering disagree where the page is genuinely ambiguous, and those are exactly
    the places worth a second look -- so both go back with the page and this says which
    one it settled on.
    """

    chosen: int = Field(ge=1)
    """1-based index into the readings that were compared."""
    confidence: Confidence
    rationale: str = Field(min_length=1, max_length=2000)


class VisualCaptionRequest(DomainModel):
    """One page's tables, charts and pictures, asked to be described.

    A caption is what the page shows about a visual and its text does not: a table names
    its columns, a chart plots a series nobody wrote out, a photograph shows a part. The
    page is sent with a crop of each unit, so the model describes what it is looking at.
    """

    request_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1)
    scope: str = Field(min_length=1, max_length=200)
    page_index: int = Field(ge=1)
    units: tuple[HierarchyUnit, ...] = Field(min_length=1, max_length=100)
    under: dict[str, str] = Field(default_factory=dict)
    """What each unit reads under, by unit ID: the headings above it, outermost first.

    A table of torque values is a table of torque values; what it says is what the section
    it sits in is arguing. Without that a caption can only describe the picture."""

    @model_validator(mode="after")
    def validate_units(self) -> VisualCaptionRequest:
        ids = tuple(unit.id for unit in self.units)
        if len(set(ids)) != len(ids):
            raise ValueError("caption request unit IDs must be unique")
        return self


CAPTION_CHARS = 6000
"""Room for a chart transcribed value by value and a list copied name by name; a caption
summarising them instead lost what a question asks for."""


class VisualCaption(DomainModel):
    subject_id: str = Field(min_length=1)
    caption: str = Field(min_length=1, max_length=CAPTION_CHARS)
    confidence: Confidence
    decorative: bool = False
    """The model found the unit carries no content of its own -- a logo, an icon, a divider.
    The unit is then dropped as an outline `drop` takes one, and no caption is written: words
    saying "a decorative icon" are not what the passage it stood in is about."""


class VisualCaptionResult(DomainModel):
    captions: tuple[VisualCaption, ...] = Field(max_length=100)

    def validate_for(self, request: VisualCaptionRequest) -> VisualCaptionResult:
        asked = {unit.id for unit in request.units}
        answered = [caption.subject_id for caption in self.captions]
        if len(answered) != len(set(answered)):
            raise ValueError("a unit is captioned more than once")
        if not set(answered) <= asked:
            raise ValueError("a caption names a unit that was not asked about")
        return self


class DocumentReferenceRequest(DomainModel):
    """One page's text, asked which other documents it names.

    A report cites a standard, a memo answers an earlier memo, a paper cites a paper. The
    name is the only handle there is -- the document being named is not here to be read --
    so what comes back is the name as the page writes it, under the unit that wrote it.
    """

    request_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1)
    scope: str = Field(min_length=1, max_length=200)
    page_index: int = Field(ge=1)
    units: tuple[HierarchyUnit, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_units(self) -> DocumentReferenceRequest:
        ids = tuple(unit.id for unit in self.units)
        if len(set(ids)) != len(ids):
            raise ValueError("reference request unit IDs must be unique")
        return self


ReferenceKind = Literal["pointer", "named", "quotation", "address"]
"""How a page makes another document a reference: an explicit expression sending the reader to
it ("see", "참고하라", "~을 보면"), the document named as a document (a report or standard
number, a file name, a title, an entry of a reference list), a quotation attributed to it, or
its address (a URL, a DOI, a file path). A method, a model or a dataset named in a sentence --
with or without a citation mark beside it -- is none of these."""


class DocumentReference(DomainModel):
    subject_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=400)
    cue: str = Field(min_length=1, max_length=400)
    """The words on the page that do the pointing, as the page writes them."""
    kind: ReferenceKind
    confidence: Confidence


class DocumentReferenceResult(DomainModel):
    references: tuple[DocumentReference, ...] = Field(max_length=200)

    def validate_for(self, request: DocumentReferenceRequest) -> DocumentReferenceResult:
        asked = {unit.id for unit in request.units}
        if any(item.subject_id not in asked for item in self.references):
            raise ValueError("a reference names a unit that was not asked about")
        return self


class FigureGroupingResult(DomainModel):
    """Which of a page's units are drawn as one figure, and nothing else.

    Asked on its own, before any text is grouped into paragraphs: what a figure swallows
    stops being text, so a paragraph can only be settled once the figures are.
    """

    figures: tuple[HierarchyFigureGroup, ...] = Field(default=(), max_length=100)


class HierarchyScopeResult(DomainModel):
    """The decisions, and nothing the caller already knows.

    A request ID and the image and manifest hashes were echoed back here and checked
    against the request. None of the three is shown to the model any more, so it could
    only invent them -- and an invented value that happened to match would prove nothing,
    because this side is the one that sent the request and knows which one it was.
    """

    decisions: tuple[HierarchyDecision, ...] = Field(min_length=1, max_length=500)

    def validate_for(self, request: HierarchyScopeRequest) -> HierarchyScopeResult:
        required = {unit.id for unit in request.units}
        subjects = [decision.subject_id for decision in self.decisions]
        if set(subjects) != required or len(subjects) != len(set(subjects)):
            raise ValueError("hierarchy result must contain exactly one decision per subject")
        # A unit still open from an earlier page is a parent this page may choose, which
        # is how one outline spans a document that does not stop at the page break.
        eligible = (
            required | {unit.id for unit in request.outline} | {request.scope_root_id}
        )
        for decision in self.decisions:
            if decision.status == "parent" and decision.parent_id not in eligible:
                raise ValueError(
                    f"hierarchy parent {decision.parent_id!r} for subject "
                    f"{decision.subject_id!r} is neither a scope unit nor the scope root"
                )
            if decision.parent_id == decision.subject_id:
                raise ValueError("hierarchy decision cannot self-parent")
        parents = {
            decision.subject_id: decision.parent_id
            for decision in self.decisions
            if decision.status == "parent" and decision.parent_id in required
        }
        for subject_id in required:
            seen: set[str] = set()
            current: str | None = subject_id
            while current in parents:
                if current in seen:
                    raise ValueError("hierarchy result contains a cycle")
                seen.add(current)
                current = parents[current]
        return self


class DocumentRecord(DomainModel):
    id: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_name: str = Field(min_length=1)
    format: DocumentFormat
    source_path: Path
    size_bytes: int = Field(ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class GraphNode(DomainModel):
    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    type: NodeType
    label: str = Field(min_length=1)
    locator: SourceLocator
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    text: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    retrievable: bool = True


class GraphEdge(DomainModel):
    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    type: EdgeType
    locator: SourceLocator | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_canonical_edge(self) -> GraphEdge:
        if self.source_id == self.target_id:
            raise ValueError("self edges are not allowed")
        if self.type is EdgeType.SAME_SHEET and self.source_id > self.target_id:
            raise ValueError("SAME_SHEET endpoints must be ordered canonically")
        return self


def native_edge(
    content_hash: str,
    document_id: str,
    source_id: str,
    target_id: str,
    edge_type: EdgeType,
    *,
    locator: SourceLocator | None = None,
) -> GraphEdge:
    """An edge the file itself states, named the same way whichever extractor read it.

    The id is derived from what the edge joins, so reading the same file again gives the
    same id -- which is what lets an unchanged document be recognised and reused. Each
    extractor spelled the formula out on its own; one of them drifting would rename every
    edge it writes.
    """
    return GraphEdge(
        id=stable_id(content_hash, f"edge:{edge_type.value}:{source_id}->{target_id}"),
        document_id=document_id,
        source_id=source_id,
        target_id=target_id,
        type=edge_type,
        locator=locator,
    )


class EvidenceRecord(DomainModel):
    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    kind: EvidenceKind
    locator: SourceLocator
    text: str | None = None
    data: JsonValue = None
    asset_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mime_type: str | None = None
    generated: bool = False

    @model_validator(mode="after")
    def require_payload(self) -> EvidenceRecord:
        if self.text is None and self.data is None and self.asset_hash is None:
            raise ValueError("evidence requires text, data, or an asset hash")
        if (
            self.kind
            in {EvidenceKind.GENERATED_EXTRACTION, EvidenceKind.GENERATED_CAPTION}
            and not self.generated
        ):
            raise ValueError("generated evidence must be marked generated")
        return self


class WarningRecord(DomainModel):
    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Severity = Severity.WARNING
    locator: SourceLocator | None = None


class ExtractedDocument(DomainModel):
    document: DocumentRecord
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    evidence: tuple[EvidenceRecord, ...]
    warnings: tuple[WarningRecord, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> ExtractedDocument:
        node_ids = {node.id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("node IDs must be unique")
        if any(node.document_id != self.document.id for node in self.nodes):
            raise ValueError("every node must belong to the document")

        edge_ids: set[str] = set()
        same_sheet_pairs: set[tuple[str, str]] = set()
        hierarchy: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            if edge.id in edge_ids:
                raise ValueError("edge IDs must be unique")
            edge_ids.add(edge.id)
            if edge.source_id not in node_ids or edge.target_id not in node_ids:
                raise ValueError("every edge endpoint must exist")
            if edge.document_id != self.document.id:
                raise ValueError("every edge must belong to the document")
            if edge.type is EdgeType.SAME_SHEET:
                pair = (edge.source_id, edge.target_id)
                if pair in same_sheet_pairs:
                    raise ValueError("SAME_SHEET pairs must be unique")
                same_sheet_pairs.add(pair)
            if edge.type in {EdgeType.CONTAINS, EdgeType.PARENT_OF}:
                hierarchy[edge.source_id].append(edge.target_id)

        state: dict[str, int] = {}

        def visit(node_id: str) -> None:
            if state.get(node_id) == 1:
                raise ValueError("hierarchy edges must be acyclic")
            if state.get(node_id) == 2:
                return
            state[node_id] = 1
            for child_id in hierarchy[node_id]:
                visit(child_id)
            state[node_id] = 2

        for node_id in node_ids:
            visit(node_id)

        evidence_node_ids = {item.node_id for item in self.evidence}
        if not evidence_node_ids.issubset(node_ids):
            raise ValueError("every evidence record must reference an existing node")
        source_evidence_node_ids = {
            item.node_id for item in self.evidence if not item.generated
        }
        missing = (
            {node.id for node in self.nodes if node.retrievable}
            - source_evidence_node_ids
        )
        if missing:
            raise ValueError("every retrievable node must have source evidence")
        return self

    def validate_canonical_semantic_graph(self) -> ExtractedDocument:
        """Validate the stricter graph contract required for newly emitted data.

        This is intentionally separate from model validation so legacy stored graphs,
        including graphs containing PARENT_OF, remain readable.
        """
        document_nodes = [
            node for node in self.nodes if node.type is NodeType.DOCUMENT
        ]
        if len(document_nodes) != 1:
            raise ValueError("canonical graph requires exactly one DOCUMENT node")
        root_id = document_nodes[0].id
        if root_id != self.document.id:
            raise ValueError("canonical DOCUMENT node must match the document ID")

        contains_parent: dict[str, str] = {}
        children_by_parent: dict[str, set[str]] = defaultdict(set)
        next_edges: list[GraphEdge] = []
        for edge in self.edges:
            if edge.type is EdgeType.PARENT_OF:
                raise ValueError("canonical graph must not contain PARENT_OF edges")
            if edge.type is EdgeType.CONTAINS:
                if edge.target_id == root_id:
                    raise ValueError("canonical DOCUMENT node must be the sole root")
                if edge.target_id in contains_parent:
                    raise ValueError(
                        "every non-root node must have exactly one incoming CONTAINS edge"
                    )
                contains_parent[edge.target_id] = edge.source_id
                children_by_parent[edge.source_id].add(edge.target_id)
            elif edge.type is EdgeType.NEXT:
                next_edges.append(edge)

        node_ids = {node.id for node in self.nodes}
        non_root_ids = node_ids - {root_id}
        if set(contains_parent) != non_root_ids:
            raise ValueError(
                "every non-root node must have exactly one incoming CONTAINS edge"
            )

        for node_id in non_root_ids:
            seen: set[str] = set()
            current = node_id
            while current != root_id:
                if current in seen:
                    raise ValueError("every node must reach the DOCUMENT root")
                seen.add(current)
                parent = contains_parent.get(current)
                if parent is None:
                    raise ValueError("every node must reach the DOCUMENT root")
                current = parent

        next_by_parent: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in next_edges:
            source_parent = contains_parent.get(edge.source_id)
            target_parent = contains_parent.get(edge.target_id)
            if source_parent is None or source_parent != target_parent:
                raise ValueError("NEXT edges must connect siblings with the same parent")
            next_by_parent[source_parent].append(edge)

        kinds = {node.id: node.type for node in self.nodes}
        for parent_id, sibling_edges in next_by_parent.items():
            predecessors: dict[str, str] = {}
            successors: dict[str, str] = {}
            for edge in sibling_edges:
                if edge.source_id in successors:
                    raise ValueError("a node may have at most one NEXT successor")
                if edge.target_id in predecessors:
                    raise ValueError("a node may have at most one NEXT predecessor")
                successors[edge.source_id] = edge.target_id
                predecessors[edge.target_id] = edge.source_id
            # What is sequenced is one kind of thing -- the slides of a deck, the pages of
            # a document. A sibling of another kind standing beside them, such as the stub
            # for another document this one names, is not a step in that sequence and
            # leaving it out of the chain is not a gap in the chain.
            chained = set(successors) | set(predecessors)
            sequenced = {
                sibling
                for sibling in children_by_parent[parent_id]
                if kinds.get(sibling) in {kinds.get(node) for node in chained}
            }
            if len(sibling_edges) != len(sequenced) - 1:
                raise ValueError("emitted NEXT edges must form a complete sibling chain")
            starts = sequenced - set(predecessors)
            if len(starts) != 1:
                raise ValueError("emitted NEXT edges must form a complete sibling chain")
            visited: set[str] = set()
            chain_node: str | None = next(iter(starts))
            while chain_node is not None:
                if chain_node in visited:
                    raise ValueError("emitted NEXT edges must form a complete sibling chain")
                visited.add(chain_node)
                chain_node = successors.get(chain_node)
            if visited != sequenced:
                raise ValueError("emitted NEXT edges must form a complete sibling chain")

        return self

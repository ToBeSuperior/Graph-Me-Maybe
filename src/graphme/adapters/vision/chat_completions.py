from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from io import BytesIO
from typing import Any, Literal, TypeVar

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from graphme.adapters.chat import (
    MALFORMED,
    Endpoint,
    complete,
    image_part,
    is_transient,
    refused,
)
from graphme.adapters.strict_schema import strict_response_schema
from graphme.adapters.vision import outline_join
from graphme.adapters.vision.prompts import (
    CAPTION_SCHEMA_NAME,
    CAPTION_SYSTEM_PROMPT,
    FIGURES_SCHEMA_NAME,
    FIGURES_SYSTEM_PROMPT,
    GROUPING_SCHEMA_NAME,
    GROUPING_SYSTEM_PROMPT,
    HIERARCHY_CHOICE_PROMPT,
    HIERARCHY_CHOICE_SCHEMA_NAME,
    HIERARCHY_CONTEXT_VERSION,
    HIERARCHY_SCHEMA_NAME,
    HIERARCHY_SYSTEM_PROMPT,
    REFERENCE_SCHEMA_NAME,
    REFERENCE_SYSTEM_PROMPT,
)
from graphme.domain.ids import canonical_json, sha256_bytes
from graphme.domain.models import (
    CAPTION_CHARS,
    DocumentReference,
    DocumentReferenceRequest,
    DocumentReferenceResult,
    FigureGroupingResult,
    HeadingLine,
    HierarchyChoice,
    HierarchyDecision,
    HierarchyFigureGroup,
    HierarchyScopeRequest,
    HierarchyScopeResult,
    HierarchyUnit,
    JoinPlacement,
    PageJoinRequest,
    ReferenceKind,
    UnitGroupingRequest,
    UnitGroupingResult,
    UnitLabel,
    VisualCaption,
    VisualCaptionRequest,
    VisualCaptionResult,
)
from graphme.providers import Wire

_log = logging.getLogger(__name__)

HIERARCHY_PROMPT_VERSION = "derived-scope-hierarchy-v2"
HIERARCHY_ATTEMPTS = 3
"""An answer that misses a subject is a slip in the answer, not a refusal, and losing a
whole scope to one is too costly; ask again before giving up."""
TRANSIENT_RETRY_DELAYS = (5.0, 15.0, 30.0, 60.0, 90.0, 120.0)
"""Seconds to wait before each retry after the endpoint turned a request away. Upstream
overload comes in bursts of minutes: attempts back to back all fell inside one, and a
20-second window still left pages flat. These retries are counted apart from the attempts
a malformed answer gets -- a busy endpoint says nothing about how the model reads the page."""
_Answer = TypeVar("_Answer")

SHORT_ID_LENGTH = 8
"""A node ID is a 36-character UUID, and against the real endpoint the model spliced one
unit's prefix onto another's suffix -- the hierarchy choice was right and the
transcription was not, and every retry went on the same clerical task. Eight characters
of a UUID separate the handful of units on one sheet and are short enough to copy."""


def short_ids(ids: Sequence[str]) -> dict[str, str]:
    """A short handle per ID, lengthened only as far as collisions require."""
    ids = list(ids)
    for length in range(SHORT_ID_LENGTH, max(len(node_id) for node_id in ids) + 1):
        short = {node_id: node_id[:length] for node_id in ids}
        if len(set(short.values())) == len(ids):
            return short
    return {node_id: node_id for node_id in ids}


def _by_handle(short: Mapping[str, str]) -> dict[str, str]:
    """The node ID each handle of `short_ids` stands for."""
    return {handle: node_id for node_id, handle in short.items()}


class _ShortIdGroup(BaseModel):
    """One unit, written as the pieces it was printed in, in reading order.

    Nothing else is asked for. A name would have to be invented and then kept consistent,
    and the order is the order the ids are written in -- both are answers this side
    already has, and every field a long answer does not carry is a field it cannot run out
    of room for.

    The ends of the run were tried instead of the pieces, to make a long paragraph cheap to
    report. It made over-joining cheap too: one careless range swallowed several
    paragraphs at once. Listing the pieces costs the answer something
    per piece, which is exactly what keeps a unit from growing past where it ends.
    """

    model_config = ConfigDict(extra="forbid")

    pieces: list[str] = Field(min_length=2, max_length=200)


class _ShortIdGroupingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[_ShortIdGroup] = Field(default_factory=list, max_length=200)

    def to_grouping_result(self, full: Mapping[str, str]) -> UnitGroupingResult:
        labels = []
        taken: set[str] = set()
        for index, group in enumerate(self.groups):
            resolved: list[str] = []
            for written in group.pieces:
                unit_id = _resolve(written, full)
                if unit_id is None:
                    # A handle with a letter dropped from it names no piece, and nothing
                    # here can tell which one it meant. The group goes; the page does not.
                    # Refusing the whole window over one mangled id left a page of
                    # paragraphs standing in single printed lines.
                    _log.warning("grouping group %d names no piece %r; dropped", index, written)
                    resolved = []
                    break
                resolved.append(unit_id)
            if not resolved:
                continue
            if taken.intersection(resolved):
                # Two units cannot hold the same piece. One group reaching into another is
                # the page misread in one place, not in all of them: the group that got
                # there first keeps its pieces, this one is dropped, and what it alone
                # covered stands on its own -- which is what an unmentioned piece does
                # anyway. Refusing the whole window over it left a page of paragraphs in
                # single printed lines.
                _log.warning("grouping group %d overlaps one already taken; dropped", index)
                continue
            taken.update(resolved)
            for order, unit_id in enumerate(resolved, start=1):
                labels.append(
                    UnitLabel(unit_id=unit_id, group=f"g{index}", order=order)
                )
        return UnitGroupingResult(labels=tuple(labels))


class _ShortIdDecision(BaseModel):
    """One decision written in shortened unit IDs."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    status: Literal["parent", "root", "unresolved", "drop"]
    parent: str | None = Field(default=None, min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=2000)


def _unit_payload(
    units: Sequence[HierarchyUnit],
    short: Mapping[str, str],
    *,
    parents: Mapping[str, str] | None = None,
    open_ids: Sequence[str] = (),
    omitted: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """The units as both hierarchy questions send them: id, label, kind, and -- where the
    rendering measured it -- which indent step the unit stands on.

    A unit carried from an earlier page also says who holds it, whether the page begins
    inside it, and how many of its own subtrees were dropped to fit.
    """
    held_by = parents or {}
    dropped = omitted or {}
    still_open = set(open_ids)
    return [
        {
            "id": short[unit.id],
            "label": unit.label,
            "node_type": unit.node_type.value,
            **({} if unit.indent is None else {"indent": unit.indent}),
            **({"parent": short[held] } if (held := held_by.get(unit.id)) else {}),
            **({"open": True} if unit.id in still_open else {}),
            **({"omitted_subtrees": count} if (count := dropped.get(unit.id)) else {}),
        }
        for unit in units
    ]


class _ReadingChoice(BaseModel):
    """Which reading the page supports, written as the number it was sent under."""

    model_config = ConfigDict(extra="forbid")

    reading: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=2000)


def _resolve(written: str, full: Mapping[str, str]) -> str | None:
    """The unit an answer names, allowing for one character gone wrong in transcription.

    A shortened id is still eight characters to copy, and now and then one comes back with
    a character dropped, added or mistyped. Three retries of the same clerical slip cost
    the page -- against a real document a page was lost to a single wrong last character.
    A slip that fits exactly one unit is that unit; anything less certain is refused,
    because a wrong parent is worse than a missing page.
    """
    written = written.strip()
    if written in full:
        return full[written]
    if len(written) < 4:
        return None
    near = [
        node_id
        for handle, node_id in full.items()
        if _one_apart(written, handle) or _tail_slipped(written, handle)
    ]
    return near[0] if len(near) == 1 else None


def _tail_slipped(written: str, handle: str) -> bool:
    """Whether only the last two characters went wrong, the rest copied exactly.

    Readings in a row wrote a handle whose last two characters were wrong, and a page's
    whole outline was lost for it. Six characters copied exactly are as good as a name: two
    handles on one page share them about once in thirty thousand, and then neither is
    chosen.
    """
    return len(written) == len(handle) > 6 and written[:-2] == handle[:-2]


def _one_apart(written: str, handle: str) -> bool:
    """Whether two ids differ by at most one character, in a string this short."""
    if abs(len(written) - len(handle)) > 1:
        return False
    if len(written) == len(handle):
        return sum(a != b for a, b in zip(written, handle, strict=True)) <= 1
    longer, shorter = (written, handle) if len(written) > len(handle) else (handle, written)
    return any(longer[:cut] + longer[cut + 1:] == shorter for cut in range(len(longer)))


class _ShortIdReference(BaseModel):
    """One named document, written under the shortened ID of the unit that names it."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=400)
    cue: str = Field(min_length=1, max_length=400)
    kind: ReferenceKind
    confidence: float = Field(ge=0.0, le=1.0)


class _ShortIdReferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    references: list[_ShortIdReference] = Field(max_length=200)

    def to_reference_result(self, full: Mapping[str, str]) -> DocumentReferenceResult:
        named: list[DocumentReference] = []
        for written in self.references:
            subject_id = _resolve(written.subject, full)
            if subject_id is None:
                continue  # a mention nobody can bind back says nothing
            named.append(
                DocumentReference(
                    subject_id=subject_id,
                    name=written.name,
                    cue=written.cue,
                    kind=written.kind,
                    confidence=written.confidence,
                )
            )
        return DocumentReferenceResult(references=tuple(named))


class _ShortIdCaption(BaseModel):
    """One caption written under the shortened unit ID it describes."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    caption: str = Field(min_length=1, max_length=CAPTION_CHARS)
    confidence: float = Field(ge=0.0, le=1.0)
    decorative: bool = False


class _ShortIdCaptionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    captions: list[_ShortIdCaption] = Field(max_length=100)

    def to_caption_result(self, full: Mapping[str, str]) -> VisualCaptionResult:
        described: list[VisualCaption] = []
        for written in self.captions:
            subject_id = _resolve(written.subject, full)
            if subject_id is None:
                # a caption nobody can bind back describes nothing
                _log.warning("caption names an unknown unit %r", written.subject[:20])
                continue
            described.append(
                VisualCaption(
                    subject_id=subject_id,
                    caption=written.caption,
                    confidence=written.confidence,
                    decorative=written.decorative,
                )
            )
        return VisualCaptionResult(captions=tuple(described))


class _ShortIdFigure(BaseModel):
    """A picture and the units drawn as part of it, in shortened unit IDs."""

    model_config = ConfigDict(extra="forbid")

    figure: str = Field(min_length=1, max_length=200)
    parts: list[str] = Field(max_length=50)


def _figure_groups(
    written: Sequence[_ShortIdFigure], full: Mapping[str, str]
) -> tuple[HierarchyFigureGroup, ...]:
    """Every named figure whose pieces bind back to units of this page."""
    groups = []
    for named in written:
        figure = _resolve(named.figure, full)
        parts = tuple(
            found
            for part in named.parts
            if (found := _resolve(part, full)) is not None and found != figure
        )
        # A figure nobody can bind back, or one with nothing drawn as part of it, says
        # nothing: the picture keeps the territory it was measured with.
        if figure is None or not parts:
            continue
        groups.append(
            HierarchyFigureGroup(figure_id=figure, member_ids=tuple(dict.fromkeys(parts)))
        )
    return tuple(groups)


class _ShortIdFigures(BaseModel):
    """The figures of one page, asked without anything else."""

    model_config = ConfigDict(extra="forbid")

    figures: list[_ShortIdFigure] = Field(default_factory=list, max_length=100)

    def to_figure_result(self, full: Mapping[str, str]) -> FigureGroupingResult:
        return FigureGroupingResult(figures=_figure_groups(self.figures, full))


class _ShortIdResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: tuple[_ShortIdDecision, ...] = Field(min_length=1, max_length=500)

    def to_scope_result(self, full: Mapping[str, str]) -> HierarchyScopeResult:
        """Resolve the shortened IDs back to the node IDs they were shortened from."""
        decisions = []
        for decision in self.decisions:
            subject = _resolve(decision.subject, full)
            if subject is None:
                raise ValueError(
                    f"unit {decision.subject!r} is not in this scope {sorted(full)}"
                )
            parent_id = None
            if decision.status == "parent":
                parent = _resolve(decision.parent or "", full)
                if parent is None:
                    raise ValueError(
                        f"parent {decision.parent!r} is not in this scope {sorted(full)}"
                    )
                parent_id = parent
            decisions.append(
                HierarchyDecision(
                    subject_id=subject,
                    status=decision.status,
                    parent_id=parent_id,
                    confidence=decision.confidence,
                    rationale=decision.rationale,
                )
            )
        return HierarchyScopeResult(decisions=tuple(decisions))


class ChatCompletionsVLMAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        client: httpx.Client,
        api_key: str | None = None,
        wire: Wire = "chat_completions",
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._endpoint = Endpoint(wire, base_url, model, api_key)
        self._client = client
        self._wait = wait

    def group_page_figures(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> FigureGroupingResult:
        """Which of this page's units are drawn as one figure, asked on its own.

        The same page and the same crops as the outline pass, and the same request to
        carry them -- but no outline travels here and nothing is placed under anything.
        This runs first, because a figure's words stop being text once it swallows them.
        """
        self._verify_hierarchy_image(request, image)
        short = short_ids([unit.id for unit in request.units])
        prompt = {
            "prompt_version": HIERARCHY_PROMPT_VERSION,
            "task": "Name every group of units this page draws as one figure.",
            "policy": {"a_drawings_labels_are_parts_of_that_drawing": True},
            "units": _unit_payload(request.units, short),
        }
        user_content = self._with_crops(request.units, image, unit_images, short, prompt)

        def read(generated: Mapping[str, Any]) -> FigureGroupingResult:
            return _ShortIdFigures.model_validate(generated).to_figure_result(_by_handle(short))

        return self._asked_until_answered(
            schema_name=FIGURES_SCHEMA_NAME,
            schema=strict_response_schema(_ShortIdFigures),
            system=FIGURES_SYSTEM_PROMPT,
            user_content=user_content,
            read=read,
            failure="the model endpoint returned a malformed figure-grouping response",
        )

    def resolve_hierarchy_scope(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
        preceding: bytes | None = None,
    ) -> HierarchyScopeResult:
        """Derive the outline of one scope from its rendered page.

        `unit_images` carries a crop for units the page alone cannot identify. A table
        names itself -- its label holds the header row and the cell range -- but a
        picture's label is "그림 5", so its crop goes along, introduced by the id the
        answer has to use.
        """
        self._verify_hierarchy_image(request, image)
        short = short_ids([unit.id for unit in (*request.units, *request.outline)])
        prompt = {
            "prompt_version": HIERARCHY_PROMPT_VERSION,
            "context_version": HIERARCHY_CONTEXT_VERSION,
            "task": (
                "Derive the parent of every unit in this complete visual scope from "
                "the rendered page."
            ),
            "policy": {
                "root_is_valid_flat_result": True,
                "a_page_break_is_not_a_new_document": True,
                "unresolved_is_explicit_abstention": True,
                "drop_removes_a_unit_that_carries_no_content": True,
                "a_marker_alone_is_not_hierarchy_authority": True,
                "a_changed_marker_with_a_changed_indent_reads_as_nesting": True,
            },
            # Only the units. Document and request identifiers and image hashes are how
            # the answer is bound back and checked here; to the model they are opaque
            # strings it cannot reason about. A unit's position in this list is not a
            # field either -- the list is already in reading order.
            "units": _unit_payload(request.units, short),
            # The outline earlier pages built. These are not decided here; naming one as a
            # parent is how a document that does not stop at the page break stays one tree.
            **(
                {
                    "outline_so_far": _unit_payload(
                        request.outline,
                        short,
                        parents=request.outline_parents,
                        open_ids=request.outline_open,
                        omitted=request.outline_omitted,
                    )
                }
                if request.outline
                else {}
            ),
        }
        user_content = self._with_crops(request.units, image, unit_images, short, prompt)
        if preceding:
            # Telling the model a heading is still open is not the same as showing it the
            # page that ended under one. A page that continues should look like one.
            user_content += [
                {"type": "text", "text": "the page before this one ended here:"},
                image_part(preceding),
            ]

        def read(generated: Mapping[str, Any]) -> HierarchyScopeResult:
            answered = _ShortIdResult.model_validate(generated)
            return answered.to_scope_result(_by_handle(short)).validate_for(request)

        return self._asked_until_answered(
            schema_name=HIERARCHY_SCHEMA_NAME,
            schema=strict_response_schema(_ShortIdResult),
            system=HIERARCHY_SYSTEM_PROMPT,
            user_content=user_content,
            read=read,
            failure="the model endpoint returned a malformed hierarchy response",
        )

    def caption_visuals(
        self,
        *,
        request: VisualCaptionRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> VisualCaptionResult:
        """Describe each of a page's visuals, shown the page and a crop of each one."""
        if not image:
            raise ValueError("caption image must not be empty")
        short = short_ids([unit.id for unit in request.units])
        prompt = {
            "prompt_version": HIERARCHY_PROMPT_VERSION,
            "task": (
                "Write what every unit listed says, from its crop and the page it is on."
            ),
            "units": [
                {
                    **written,
                    **(
                        {"under": request.under[unit.id]}
                        if request.under.get(unit.id)
                        else {}
                    ),
                }
                for unit, written in zip(
                    request.units, _unit_payload(request.units, short), strict=True
                )
            ],
        }
        content = self._with_crops(request.units, image, unit_images, short, prompt)

        def read(generated: Mapping[str, Any]) -> VisualCaptionResult:
            answered = _ShortIdCaptionResult.model_validate(generated)
            return answered.to_caption_result(_by_handle(short)).validate_for(request)

        return self._asked_until_answered(
            schema_name=CAPTION_SCHEMA_NAME,
            schema=strict_response_schema(_ShortIdCaptionResult),
            system=CAPTION_SYSTEM_PROMPT,
            user_content=content,
            read=read,
            failure="the model endpoint returned a malformed caption response",
        )

    def group_page_units(
        self,
        *,
        request: UnitGroupingRequest,
        images: Sequence[bytes],
    ) -> UnitGroupingResult:
        """Ask one window of pages which of its pieces are one unit."""
        if not images or any(not image for image in images):
            raise ValueError("grouping images must not be empty")
        short = short_ids([unit.id for unit in request.units])
        prompt = {
            "prompt_version": HIERARCHY_PROMPT_VERSION,
            "task": "Name every unit printed in more than one piece, by its pieces in order.",
            "pieces": [
                {
                    "id": short[unit.id],
                    "page": unit.page_index,
                    "text": unit.text,
                    # Decided by the window before; carry it on, do not take it apart.
                    **({"settled": True} if unit.id in request.settled else {}),
                }
                for unit in request.units
            ],
        }
        content: list[dict[str, Any]] = [
            {"type": "text", "text": canonical_json(prompt)},
            *(image_part(image) for image in images),
        ]
        full = _by_handle(short)

        def read(generated: Mapping[str, Any]) -> UnitGroupingResult:
            answered = _ShortIdGroupingResult.model_validate(generated)
            return answered.to_grouping_result(full).validate_for(request)

        return self._asked_until_answered(
            schema_name=GROUPING_SCHEMA_NAME,
            schema=strict_response_schema(_ShortIdGroupingResult),
            system=GROUPING_SYSTEM_PROMPT,
            user_content=content,
            read=read,
            failure="the model endpoint returned a malformed grouping response",
        )

    def find_references(
        self,
        *,
        request: DocumentReferenceRequest,
        image: bytes,
        unit_images: Mapping[str, bytes] | None = None,
    ) -> DocumentReferenceResult:
        """Read one page for the other documents it names.

        The page is what is read, the way the hierarchy pass reads one: a unit goes out as
        its id and its label, and a table, a chart or a picture goes out as its crop. The
        values inside a table are not sent -- they are on the page already.
        """
        if not image:
            raise ValueError("reference image must not be empty")
        short = short_ids([unit.id for unit in request.units])
        prompt = {
            "prompt_version": HIERARCHY_PROMPT_VERSION,
            "task": (
                "List every other document this page explicitly cites, under the unit citing it."
            ),
            "units": _unit_payload(request.units, short),
        }

        def read(generated: Mapping[str, Any]) -> DocumentReferenceResult:
            answered = _ShortIdReferenceResult.model_validate(generated)
            return answered.to_reference_result(_by_handle(short)).validate_for(request)

        return self._asked_until_answered(
            schema_name=REFERENCE_SCHEMA_NAME,
            schema=strict_response_schema(_ShortIdReferenceResult),
            system=REFERENCE_SYSTEM_PROMPT,
            user_content=self._with_crops(request.units, image, unit_images, short, prompt),
            read=read,
            failure="the model endpoint returned a malformed reference response",
        )

    def outline_headings(
        self, *, lines: Sequence[HeadingLine]
    ) -> tuple[tuple[str, str | None], ...]:
        """Which text units are the document's headings, each with the one it reads under.
        Text only: every unit of the document on a line, so a heading is known by what
        stands around it across pages."""
        short = short_ids([line.unit_id for line in lines])
        full = _by_handle(short)
        return self._asked_until_answered(
            schema_name=outline_join.HEADINGS_SCHEMA_NAME,
            schema=strict_response_schema(outline_join.Headings),
            system=outline_join.HEADINGS_SYSTEM_PROMPT,
            user_content=outline_join.heading_lines(lines, short),
            read=lambda generated: outline_join.read_headings(
                generated, lambda written: _resolve(written, full)),
            failure="the model endpoint returned a malformed headings response",
        )

    def join_page(self, *, request: PageJoinRequest) -> tuple[JoinPlacement, ...]:
        """Where one page's top units go in the outline of the pages before it. Text only."""
        short = short_ids([top.unit_id for top in request.tops])
        full = _by_handle(short)
        return self._asked_until_answered(
            schema_name=outline_join.JOIN_SCHEMA_NAME,
            schema=strict_response_schema(outline_join.Joined),
            system=outline_join.JOIN_SYSTEM_PROMPT,
            user_content=outline_join.join_text(request, short),
            read=lambda generated: outline_join.read_join(
                generated, request, lambda written: _resolve(written, full)),
            failure="the model endpoint returned a malformed join response",
        )

    def choose_hierarchy_scope(
        self,
        *,
        request: HierarchyScopeRequest,
        image: bytes,
        candidates: Sequence[HierarchyScopeResult],
        unit_images: Mapping[str, bytes] | None = None,
    ) -> HierarchyChoice:
        """Show two readings of one page back with the page and keep the better one.

        Each reading is the only witness to its own answer, and they disagree exactly
        where the page is ambiguous. The outlines go back written in the same shortened
        ids, so choosing is reading the page again rather than re-deriving it.
        """
        if len(candidates) < 2:
            raise ValueError("choosing a reading needs at least two of them")
        self._verify_hierarchy_image(request, image)
        short = short_ids([unit.id for unit in (*request.units, *request.outline)])
        prompt = {
            "prompt_version": HIERARCHY_PROMPT_VERSION,
            "context_version": HIERARCHY_CONTEXT_VERSION,
            "task": (
                "Two readings of this page disagree. Decide which one the page supports."
            ),
            "policy": {
                "judge_the_reading_not_the_wording": True,
                "a_whole_reading_is_chosen_not_merged": True,
            },
            "units": _unit_payload(request.units, short),
            "readings": [
                {
                    "number": number,
                    "outline": [
                        {
                            "unit": short[decision.subject_id],
                            "status": decision.status,
                            # A parent can be a unit on this page, one still open from
                            # an earlier one, or the scope root -- which is not a unit and
                            # is left unsaid, the way a root decision leaves it unsaid.
                            **(
                                {"parent": short[decision.parent_id]}
                                if decision.parent_id in short
                                else {}
                            ),
                        }
                        for decision in reading.decisions
                        if decision.subject_id in short
                    ],
                }
                for number, reading in enumerate(candidates, start=1)
            ],
        }
        def read(generated: Mapping[str, Any]) -> HierarchyChoice:
            answered = _ReadingChoice.model_validate(generated)
            if not 1 <= answered.reading <= len(candidates):
                raise ValueError("chosen reading is not one of the readings sent")
            return HierarchyChoice(
                chosen=answered.reading,
                confidence=answered.confidence,
                rationale=answered.rationale,
            )

        return self._asked_until_answered(
            schema_name=HIERARCHY_CHOICE_SCHEMA_NAME,
            schema=strict_response_schema(_ReadingChoice),
            system=HIERARCHY_CHOICE_PROMPT,
            user_content=self._with_crops(request.units, image, unit_images, short, prompt),
            read=read,
            failure="the model endpoint returned a malformed hierarchy choice",
        )

    def _asked_until_answered(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system: str,
        user_content: str | list[dict[str, Any]],
        read: Callable[[Mapping[str, Any]], _Answer],
        failure: str,
    ) -> _Answer:
        """Ask until the answer is usable, or give up saying so.

        Two kinds of failure are retried on the same footing. A malformed answer is the
        model getting the shape wrong; a timeout or a 5xx is the endpoint not answering at
        all -- readings run together and one can be turned away while the other succeeds.
        Neither says anything about the page, so both are worth asking again.
        """
        turned_away = 0
        attempt = 0
        while attempt < HIERARCHY_ATTEMPTS:
            last = attempt + 1 == HIERARCHY_ATTEMPTS
            try:
                generated = self._structured_completion(
                    schema_name=schema_name,
                    schema=schema,
                    system=system,
                    user_content=user_content,
                )
            except httpx.HTTPError as error:
                if isinstance(error, httpx.HTTPStatusError) and refused(error):
                    # This request turned down -- an image the provider will not take: the
                    # page goes unanswered, as a malformed answer does, saying why.
                    attempt += 1
                    if last:
                        raise RuntimeError(f"{failure}: {error.response.status_code} "
                                           f"{error.response.text[:300]}") from error
                    continue
                if not is_transient(error):
                    raise
                if turned_away == len(TRANSIENT_RETRY_DELAYS):
                    # Out of retries is the page going unanswered, the same as a malformed
                    # answer: the caller leaves this page and reads the rest.
                    raise RuntimeError(
                        f"the endpoint did not answer after {turned_away + 1} attempts: {error}"
                    ) from error
                self._wait(TRANSIENT_RETRY_DELAYS[turned_away])
                turned_away += 1
                continue
            except RuntimeError as error:  # an answer that is not JSON at all: malformed too
                attempt += 1
                if last:
                    raise RuntimeError(failure) from error
                continue
            attempt += 1
            try:
                return read(generated)
            except (ValidationError, ValueError, TypeError) as error:
                if last:
                    raise RuntimeError(failure) from error
        raise RuntimeError(failure)

    @staticmethod
    def _verify_hierarchy_image(request: HierarchyScopeRequest, image: bytes) -> None:
        """The page is what the answer is judged against, so it is checked before it goes."""
        if not image:
            raise ValueError("hierarchy image must not be empty")
        if sha256_bytes(image) != request.image.asset_hash:
            raise ValueError("hierarchy image hash does not match request")
        if len(image) != request.image.size_bytes:
            raise ValueError("hierarchy image size does not match request")
        try:
            with Image.open(BytesIO(image)) as decoded:
                if decoded.format != "PNG":
                    raise ValueError("hierarchy image must be PNG")
                if decoded.size != (request.image.width, request.image.height):
                    raise ValueError("hierarchy image dimensions do not match request")
        except (OSError, ValueError) as error:
            raise ValueError("hierarchy image cannot be decoded as declared") from error

    @staticmethod
    def _with_crops(
        units: Sequence[HierarchyUnit],
        image: bytes,
        unit_images: Mapping[str, bytes] | None,
        short: Mapping[str, str],
        prompt: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """The page, then every crop introduced by the id the answer has to use.

        The number the answer must use and the label the model can read sit next to the
        picture, so identifying it is not a join back into the unit list.
        """
        content: list[dict[str, Any]] = [
            {"type": "text", "text": canonical_json(prompt)},
            image_part(image),
        ]
        by_id = {unit.id: unit for unit in units}
        for unit_id, crop in (unit_images or {}).items():
            unit = by_id.get(unit_id)
            if unit is None:
                raise ValueError(f"{unit_id} is not a scope unit")
            if not crop:
                raise ValueError(f"crop for {unit_id} must not be empty")
            content += [
                {"type": "text", "text": f"unit {short[unit.id]} ({unit.label}):"},
                image_part(crop),
            ]
        return content

    def _structured_completion(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system: str,
        user_content: str | list[dict[str, Any]],
    ) -> Any:
        text = complete(self._client, self._endpoint, system=system, content=user_content,
                        schema=schema, name=schema_name)
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(MALFORMED) from error

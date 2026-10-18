"""The passages a question is searched by: the outline cut into pieces worth reading alone.

One node at a time says too little -- a line such as "below the reference value", a heading
with nothing under it. A whole document says too much. So the outline is cut where it
already joins things: a section that fits the budget is one passage, heading and all; a
section that does not is cut between its items, and each piece is read under the headings
above it. Small neighbouring sections share a passage rather than each standing alone. A
single unit bigger than a passage -- a long table -- is cut between its lines, and a line
bigger than a passage inside it, so every part of it is in some passage.

Every unit is covered by a passage, whether or not it says anything: a heading read as the
path above the pieces of its section belongs to the first of them, a picture with no words
to the passage it stands in, and a caption to the passage of what it describes. A page
number standing alone directly under the document -- a short number, nothing else -- is
covered but never read into a passage: a passage of only "2 / 3 / 4" is nothing a question
is about, and a linked document once showed one as its nearest. A document number or a date
under the document, and every value inside a section, stay what the passage says.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from graphme.application.text_match import estimated_tokens

CHUNK_TOKENS = 600
"""A search unit: a section of a few paragraphs, or a table with the line that names it."""
HEADING_TOKENS = 40
"""A section too big for one passage gives its own words to the headings above each piece --
unless it says more than a heading does, and then they are read as part of the first."""
PATH_LABEL_CHARS = 80
CHUNK_RULE = f"outline/{CHUNK_TOKENS}/{HEADING_TOKENS}/lines/covers-all/words"
"""Names how passages are cut. Stored on each passage, so a document cut by another rule is
cut again; change it whenever `chunks` would cut the same outline differently."""


@dataclass(frozen=True, slots=True)
class GraphUnit:
    id: str
    parent: str | None
    """Its parent in the outline; None directly under the document."""
    text: str
    order: tuple[float, ...]
    """Where it stands in reading order, among its siblings."""
    attached: tuple[str, ...] = ()
    """Nodes read with it and not on their own -- a visual's captions: its passage covers
    them too."""


@dataclass(frozen=True, slots=True)
class Chunk:
    member_ids: tuple[str, ...]
    text: str


def chunks(title: str, units: Sequence[GraphUnit], *, budget: int = CHUNK_TOKENS) -> list[Chunk]:
    by_id = {unit.id: unit for unit in units}
    children: dict[str | None, list[GraphUnit]] = {}
    for unit in units:
        parent = unit.parent if unit.parent in by_id else None
        children.setdefault(parent, []).append(unit)
    for siblings in children.values():
        siblings.sort(key=lambda unit: unit.order)

    sizes: dict[str, int] = {}

    def size(unit: GraphUnit) -> int:
        if unit.id not in sizes:
            sizes[unit.id] = estimated_tokens(unit.text) + sum(
                size(child) for child in children.get(unit.id, ())
            )
        return sizes[unit.id]

    def subtree(unit: GraphUnit) -> list[GraphUnit]:
        return [unit, *(u for child in children.get(unit.id, ()) for u in subtree(child))]

    out: list[Chunk] = []
    pending: list[str] = []
    """Units no passage has covered yet -- a heading gone into the path, a stretch with
    nothing to say -- given to the next passage."""

    def emit(path: list[str], members: list[GraphUnit]) -> None:
        # A page number standing alone under the document is covered but not read.
        said = [unit for unit in members
                if unit.text and not (unit.parent not in by_id and _page_number(unit.text))]
        covered = [*pending, *(i for unit in members for i in (unit.id, *unit.attached))]
        head = " > ".join(path)
        if not said:
            pending[:] = covered
            return
        pending.clear()
        if len(said) == 1 and estimated_tokens(said[0].text) > budget:
            pieces = _pieces(said[0].text, budget)
            out.extend(
                Chunk(member_ids=tuple(covered) if index == 0 else (said[0].id,),
                      text=f"{head}\n{piece}")
                for index, piece in enumerate(pieces)
            )
        else:
            out.append(
                Chunk(
                    member_ids=tuple(covered),
                    text="\n".join([head, *(unit.text for unit in said)]),
                )
            )

    def walk(parent: str | None, path: list[str], lead: list[GraphUnit]) -> None:
        group, used = list(lead), sum(estimated_tokens(unit.text) for unit in lead)
        for child in children.get(parent, ()):
            if size(child) <= budget:
                if group and used + size(child) > budget:
                    emit(path, group)
                    group, used = [], 0
                group += subtree(child)
                used += size(child)
                continue
            emit(path, group)
            group, used = [], 0
            if estimated_tokens(child.text) > HEADING_TOKENS:
                walk(child.id, path, [child])
            else:
                pending.extend((child.id, *child.attached))
                walk(child.id, [*path, child.text[:PATH_LABEL_CHARS]], [])
        emit(path, group)

    walk(None, [title], [])
    if pending and out:
        # What came after the last words is the last passage's.
        last = out[-1]
        out[-1] = Chunk(member_ids=(*last.member_ids, *pending), text=last.text)
    elif pending:
        # Nothing said anything -- page numbers alone: one passage by the title covers it.
        out.append(Chunk(member_ids=tuple(pending), text=title))
    return out


_PAGE_NUMBER = re.compile(r"[\s\-–—]*\d{1,4}[\s\-–—]*")


def _page_number(text: str) -> bool:
    """Whether `text` is a page number and nothing else -- `7`, `- 12 -` -- not a document
    number, a date or a value with a point in it."""
    return bool(_PAGE_NUMBER.fullmatch(text))


def _pieces(text: str, budget: int) -> list[str]:
    """`text` cut between its lines into pieces of at most `budget`, and a line longer than
    that cut inside it."""
    lines: list[str] = []
    for line in text.split("\n"):
        while estimated_tokens(line) > budget:
            cut = _fitting(line, budget)
            lines.append(line[:cut])
            line = line[cut:]
        lines.append(line)
    pieces: list[str] = []
    for line in lines:
        if pieces and estimated_tokens(f"{pieces[-1]}\n{line}") <= budget:
            pieces[-1] = f"{pieces[-1]}\n{line}"
        else:
            pieces.append(line)
    return pieces


def _fitting(line: str, budget: int) -> int:
    """The longest start of `line` within `budget`, and at least one character."""
    low, high = 1, len(line)
    while low < high:
        middle = (low + high + 1) // 2
        if estimated_tokens(line[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


"""The units the page says are one unit, made into one.

A document is printed, not written down: a title wraps, a paragraph runs over a column, a
slide puts one thought in two shapes. What the file hands over is the pieces; which of
them are one thing is what the page shows and the page is asked.

Deciding is the page's job. Joining is arithmetic -- given the pieces of a unit, its text,
its box and its place follow -- so it is done here, once, and the same way every time.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from graphme.application.page_units import held_by_a_visual
from graphme.application.reading_order import (
    SAME_COLUMN_OVERLAP,
    overlap,
    reading_order,
    stated_box,
)
from graphme.domain.enums import EdgeType, EvidenceKind, NodeType
from graphme.domain.ids import stable_id
from graphme.domain.models import (
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    GroupableUnit,
    UnitGroupingRequest,
    UnitGroupingResult,
    UnitLabel,
)

_log = logging.getLogger(__name__)

JOINED_FROM = "joined_from"
"""The pieces a unit was printed in, in reading order, kept on the unit that holds them."""


PIECES_PER_ASK = 80
"""How many pieces one answer has to carry. A dense page prints many times this many
lines, and an answer naming every piece of every paragraph among them came back with a
paragraph's first few lines and the rest abandoned -- a different few in each reading of
the same page. Asked in shorter runs, an answer is short enough to finish.

Pages that follow fill a run while they fit, so a page of a few lines is not an ask of its
own. Twice this many, packed the same way, joined across what the page showed apart."""

SMALL_PRINT_MARGIN = 0.5
"""How much smaller than the running text a piece has to be to be small print. A page's
text is set within a fraction of a point of one size; a footnote is a whole point or two
below it."""

CARRY_PIECES = 3
"""How much of the ask before comes along. Only its end can be carried on, and asking
about the rest invites the page to be taken apart again -- which is how a window came to
argue with the one before it and have its whole answer thrown away."""


def group_document_units(
    extracted: ExtractedDocument,
    *,
    pages: Mapping[int, bytes],
    vision: Any,
    join: Callable[[Sequence[str]], str] = lambda texts: " ".join(texts),
    page_of: Mapping[str, int] | None = None,
) -> ExtractedDocument:
    """Ask each page, a run of pieces at a time, which pieces are one unit, and join them.

    `page_of` says where each unit was drawn when the units do not: a Word paragraph lands
    on whatever page the layout puts it.

    Each ask carries the last few pieces of the one before, marked settled, and shows both
    pages, so a unit broken by a page break or a run's end keeps one name on both sides.

    Each ask names its groups from scratch, so a name is only the same group when it was
    carried in as settled; everything else is kept apart by the ask it came from.
    """
    units = groupable_units(extracted, page_of)
    if not units:
        return extracted
    by_page: dict[int, list[GroupableUnit]] = {}
    for unit in units:
        by_page.setdefault(unit.page_index, []).append(unit)
    # Small print -- footnotes, captions, the page number -- is read once a page's text is
    # done, so it goes to the page's end: left where it is printed, it stood between the
    # last line of one column and the first of the next, and the two were never joined.
    small = _small_print(extracted, by_page)
    by_page = {
        page: [u for u in held if u.id not in small] + [u for u in held if u.id in small]
        for page, held in by_page.items()
    }

    # A page is asked in runs short enough for one answer to carry. Whatever is left of a
    # page goes into the next ask, which is the same thing a page break already was.
    asks = _asks(by_page)
    named: dict[str, str] = {}

    before: list[GroupableUnit] = []
    earlier: int | None = None
    for index, (pages_asked, scope, pieces) in enumerate(asks):
        page = pages_asked[-1]
        # Only the end of what was asked before can be carried on. The rest was settled
        # where it stood, and asking again invites a page to be taken apart twice.
        # What carries on is the text: a page that ends in footnotes carried only those,
        # and its last line never met the line on the next page that finishes it.
        tail = [unit for unit in before if unit.id not in small][-CARRY_PIECES:]
        shown = [*tail, *pieces]
        if not shown:
            continue
        settled = {unit.id: named[unit.id] for unit in tail if unit.id in named}
        request = UnitGroupingRequest(
            request_id=f"{extracted.document.id}:groups:{index:03d}",
            document_id=extracted.document.id,
            scope=scope,
            units=tuple(shown),
            settled=settled,
        )
        images = [
            pages[shown_page]
            for shown_page in dict.fromkeys(
                (earlier, *pages_asked) if earlier is not None else pages_asked
            )
            if shown_page in pages
        ]
        try:
            answer = vision.group_page_units(request=request, images=images)
        except (RuntimeError, ValueError) as error:
            # One page nobody could read is one page, not the document. What the answer
            # was refused for travels with it: "malformed" alone says nothing about which
            # rule the answer broke, and that is the one thing worth knowing here.
            _log.warning(
                "%s: units left as they were -- %s%s",
                request.scope,
                error,
                f" ({error.__cause__})" if error.__cause__ else "",
            )
            before, earlier = pieces, page
            continue
        # Every window names its groups from scratch, so a name says nothing across the
        # break. What a group carries on is said by holding a piece that is already named.
        members: dict[str, list[str]] = {}
        for label in sorted(answer.labels, key=lambda item: item.order):
            members.setdefault(label.group, []).append(label.unit_id)
        for group, unit_ids in members.items():
            carried = {named[unit_id] for unit_id in unit_ids if unit_id in named}
            name = carried.pop() if carried else f"w{index:03d}-{group}"
            for unit_id in unit_ids:
                named.setdefault(unit_id, name)
        before, earlier = pieces, page
    # The order inside a group is the order the document is read in, which is already
    # known here -- an answer only has to say what belongs together.
    in_order: dict[str, list[str]] = {}
    for unit in units:
        if unit.id in named:
            in_order.setdefault(named[unit.id], []).append(unit.id)
    return _joined_groups(extracted, list(in_order.values()), join)


def _asks(
    by_page: Mapping[int, Sequence[GroupableUnit]],
) -> list[tuple[tuple[int, ...], str, list[GroupableUnit]]]:
    """The runs a document is asked in, in reading order: the pages each shows, its scope,
    and its pieces. A page is cut into runs of `PIECES_PER_ASK`, and a page that fits whole
    in what the run before left joins it."""
    runs: list[tuple[tuple[int, ...], str, list[GroupableUnit]]] = []
    for page in sorted(by_page):
        held = list(by_page[page])
        cut = [held[at : at + PIECES_PER_ASK] for at in range(0, len(held), PIECES_PER_ASK)]
        for run, pieces in enumerate(cut):
            last = runs[-1] if runs else None
            if run == 0 and last is not None and len(last[2]) + len(pieces) <= PIECES_PER_ASK:
                shown = (*last[0], page)
                runs[-1] = (shown, f"pages-{shown[0]:03d}-{page:03d}", [*last[2], *pieces])
                continue
            # A page asked in one run is named after itself, as it always was; only a page
            # that had to be split says which of its runs this is.
            scope = f"page-{page:03d}" if run == 0 else f"page-{page:03d}-{run + 1}"
            runs.append(((page,), scope, pieces))
    return runs


def _small_print(
    extracted: ExtractedDocument, by_page: Mapping[int, Sequence[GroupableUnit]]
) -> frozenset[str]:
    """Pieces printed smaller than their page's text, with no text above or below them.

    The running text is whatever size most of the page is set in. Small print with none of
    it underneath, in the same stretch of the page's width -- a footnote, the page number --
    or none of it above -- the caption of a figure standing at the top -- is the page's
    furniture, read once its text is done. Small print with text on both sides stays where
    it is: it is a note on the paragraph above it, and a report's notes sent to the page's
    end came back cut off from the lines they explain.

    What is above and below is judged by the width a piece shares with the text, not by
    column: on a single-column page every line of text crosses most of the page, and a
    note's short second line, taken for a column of its own, went to the end alone. A piece
    with no size, from a format that does not say, is never small.
    """
    size = {node.id: node.metadata.get("font_size") for node in extracted.nodes}
    box = {node.id: stated_box(node.metadata) for node in extracted.nodes}
    small: set[str] = set()
    for held in by_page.values():
        known = {
            u.id: float(value)
            for u in held
            if isinstance(value := size.get(u.id), int | float)
        }
        if not known:
            continue
        body = Counter(round(value * 2) / 2 for value in known.values()).most_common(1)[0][0]
        text = [
            found
            for u in held
            if known.get(u.id, body) >= body - SMALL_PRINT_MARGIN
            and (found := box.get(u.id)) is not None
        ]
        for u in held:
            found = box.get(u.id)
            if found is None or known.get(u.id, body) >= body - SMALL_PRINT_MARGIN:
                continue
            beside = [
                other
                for other in text
                if overlap((found[0], found[2]), other) >= SAME_COLUMN_OVERLAP
            ]
            if not any(other[1] > found[1] for other in beside) or not any(
                other[1] < found[1] for other in beside
            ):
                small.add(u.id)
    return frozenset(small)


def explode_text(
    extracted: ExtractedDocument,
    *,
    pieces: Mapping[str, Sequence[str]] | None = None,
) -> ExtractedDocument:
    """Hand every unit over in the pieces it was printed in.

    What the file gives is not what the page shows. A PDF gives runs of text grouped by
    where the ink fell, so `pieces` says which printed lines each run holds. A slide gives
    a shape holding several paragraphs, already joined by newlines, so there is nothing to
    look up -- the newlines say it.

    Nothing is decided here. A unit printed as one piece comes back untouched, and the
    first piece of every other keeps the node everything already points at.
    """
    split: dict[str, tuple[str, ...]] = {}
    for node in extracted.nodes:
        if node.type is not NodeType.TEXT_CHUNK or not (node.text or "").strip():
            continue
        printed = (
            tuple(pieces[node.id])
            if pieces is not None and node.id in pieces
            else tuple(part for part in (node.text or "").split("\n") if part.strip())
        )
        if len(printed) > 1:
            split[node.id] = printed
    if not split:
        return extracted

    holders = {
        edge.target_id: edge
        for edge in extracted.edges
        if edge.type is EdgeType.CONTAINS
    }
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = list(extracted.edges)
    evidence: list[EvidenceRecord] = [
        record for record in extracted.evidence if record.node_id not in split
    ]
    for node in extracted.nodes:
        parts = split.get(node.id)
        if parts is None:
            nodes.append(node)
            continue
        for index, text in enumerate(parts):
            piece = node.model_copy(
                update={
                    "id": node.id if index == 0 else _piece_id(extracted, node, index),
                    "label": " ".join(text.split())[:80] or node.label,
                    "text": text,
                    "metadata": {**node.metadata, "printed_piece": index},
                }
            )
            nodes.append(piece)
            held_by = holders.get(node.id)
            if index and held_by is not None:
                edges.append(
                    held_by.model_copy(
                        update={
                            "id": f"contains:{held_by.source_id}->{piece.id}",
                            "target_id": piece.id,
                        }
                    )
                )
            evidence.append(
                EvidenceRecord(
                    id=stable_id(
                        extracted.document.content_hash, f"evidence:piece:{piece.id}"
                    ),
                    document_id=extracted.document.id,
                    node_id=piece.id,
                    kind=EvidenceKind.SOURCE_TEXT,
                    locator=piece.locator,
                    text=text,
                )
            )
    return extracted.model_copy(
        update={
            "nodes": tuple(nodes),
            "edges": tuple(edges),
            "evidence": tuple(evidence),
        }
    )


def _piece_id(extracted: ExtractedDocument, node: GraphNode, index: int) -> str:
    return stable_id(extracted.document.content_hash, f"{node.id}:printed-{index}")


def groupable_units(
    extracted: ExtractedDocument, page_of: Mapping[str, int] | None = None
) -> tuple[GroupableUnit, ...]:
    """Every piece of text still standing on its own, in the order the document is read.

    What a picture swallowed is the picture's -- by the binding, which hands it whatever
    it encloses, or by the page, which says what else is drawn with it. Neither is a unit
    to settle here, and sending one invites a figure's words to be read as a paragraph.
    """
    swallowed = held_by_a_visual(extracted)
    units = [
        node
        for node in extracted.nodes
        if node.type is NodeType.TEXT_CHUNK
        and (node.text or "").strip()
        and node.id not in swallowed
    ]
    if page_of:
        # Drawn by a layout, the units are read in the file's own order; one the layout
        # did not place is where the unit before it was.
        placed: list[tuple[GraphNode, int]] = []
        for node in units:
            placed.append((node, page_of.get(node.id, placed[-1][1] if placed else 1)))
    else:
        placed = [(node, _page(node)) for node in in_reading_order(units)]
    return tuple(
        GroupableUnit(
            id=node.id,
            text=(node.text or "").strip()[:4000],
            node_type=node.type,
            page_index=page,
        )
        for node, page in placed
    )


def in_reading_order(units: Sequence[GraphNode]) -> list[GraphNode]:
    """Every page's units in the order they are read, page after page."""
    by_page: dict[int, list[GraphNode]] = {}
    for unit in units:
        by_page.setdefault(_page(unit), []).append(unit)
    return [
        unit
        for page in sorted(by_page)
        for unit in reading_order(by_page[page], lambda u: stated_box(u.metadata), width=1.0)
    ]


def _page(node: GraphNode) -> int:
    return max(1, node.locator.page or node.locator.slide or 1)


def apply_groups(
    extracted: ExtractedDocument,
    grouping: UnitGroupingResult | Sequence[UnitLabel],
    *,
    join: Callable[[Sequence[str]], str] = lambda texts: " ".join(texts),
) -> ExtractedDocument:
    """Join every group of pieces into the first piece, and drop the rest.

    The first piece keeps its id: everything that already points at this unit -- an edge,
    a piece of evidence, a decision made about it -- goes on pointing at something real.

    What arrives is one ask's answer or the labels a whole document's asks came to. A page
    of a paper holds many times the pieces a slide does, so the cap that keeps one answer
    readable is not a cap on how much of a document may be grouped.
    """
    labels = grouping.labels if isinstance(grouping, UnitGroupingResult) else tuple(grouping)
    known = {node.id: node for node in extracted.nodes}
    for label in labels:
        if label.unit_id not in known:
            raise ValueError(f"{label.unit_id} is not in the document")

    groups: dict[str, list[tuple[int, str]]] = {}
    for label in labels:
        groups.setdefault(label.group, []).append((label.order, label.unit_id))
    return _joined_groups(
        extracted,
        [[unit_id for _, unit_id in sorted(members)] for members in groups.values()],
        join,
    )


def _joined_groups(
    extracted: ExtractedDocument,
    groups: Sequence[Sequence[str]],
    join: Callable[[Sequence[str]], str],
) -> ExtractedDocument:
    """Each group of pieces, in reading order, joined into its first piece."""
    known = {node.id: node for node in extracted.nodes}
    joined = {pieces[0]: pieces for pieces in groups if len(pieces) > 1}
    if not joined:
        return extracted

    absorbed = {piece for pieces in joined.values() for piece in pieces[1:]}
    nodes = tuple(
        _joined(known, run, join) if (run := joined.get(node.id)) else node
        for node in extracted.nodes
        if node.id not in absorbed
    )
    return extracted.model_copy(
        update={
            "nodes": nodes,
            "edges": tuple(_kept_edges(extracted.edges, absorbed)),
            "evidence": tuple(
                record for record in extracted.evidence if record.node_id not in absorbed
            ),
        }
    )


def _joined(
    known: dict[str, GraphNode],
    pieces: Sequence[str],
    join: Callable[[Sequence[str]], str],
) -> GraphNode:
    held = [known[piece] for piece in pieces]
    text = join([node.text or "" for node in held])
    box = _around(held)
    return held[0].model_copy(
        update={
            "label": " ".join(text.split())[:80] or held[0].label,
            "text": text,
            "metadata": {
                **held[0].metadata,
                **({"normalized_bbox": box} if box else {}),
                JOINED_FROM: list(pieces),
                "decided_by": "page",
            },
        }
    )


def _around(held: Sequence[GraphNode]) -> list[float] | None:
    """The box around what the unit printed where it starts: its own page, its own column.

    A unit lives where its first piece is. What it carries onto the next page is measured
    on another sheet, and what it carries into the next column stands beside it, not under
    it. Unioned, either one gave a paragraph a box running the page's width from the top of
    where it resumed -- and it was read, and placed in the outline, as though it stood up
    there: a section's first paragraph arrived ahead of two headings printed above it.
    """
    home = _page(held[0])
    start = stated_box(held[0].metadata)
    boxes = [
        box
        for node in held
        if _page(node) == home
        and (box := stated_box(node.metadata)) is not None
        and (start is None or overlap((start[0], start[2]), box) >= SAME_COLUMN_OVERLAP)
    ]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _kept_edges(
    edges: Sequence[GraphEdge], absorbed: set[str]
) -> list[GraphEdge]:
    """Every edge that still has both ends. A piece that is gone takes its edges with it."""
    return [
        edge
        for edge in edges
        if edge.source_id not in absorbed and edge.target_id not in absorbed
    ]



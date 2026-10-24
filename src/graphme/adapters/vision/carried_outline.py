"""What a flowing document's page is shown of the document's headings before it.

The headings travel as the outline itself, not a chain: the open chain down to the last
heading before the page, then the headings shallowest first, each only with its parent
already carried, up to the budget. A heading that arrives without its items says how many it
holds, or it reads as a section that began there and holds nothing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from graphme.application.hierarchy_decisions import dropped_subjects
from graphme.application.page_hierarchy_scope import HierarchyScopeUnits
from graphme.application.visual_crops import VISUAL_TYPES
from graphme.domain.models import HierarchyDecision, HierarchyUnit
from graphme.domain.text_match import estimated_tokens

CARRY_TOKEN_BUDGET = 1200
"""How much of the outline built so far travels to the next page. It is a reference the
page reads its own units against, so it is worth twice a search unit: too little and the
page sees a section it cannot recognise, too much and the model reaches past what the
page can settle."""

CARRY_LABEL_CHARS = 80
"""A carried heading is there to be recognised, not read. Its opening words do that."""

CARRY_ITEM_CHARS = 40
"""A carried unit holding nothing -- a paragraph, a list item -- is there to show what kind
of thing reads under its heading, and fewer words than a heading's do that."""

SKELETON_SHARE = 0.5
"""How much of the budget the document's headings may take, shallowest first. The rest
goes to whole recent sections. A long document's headings alone can outgrow the budget,
so its deepest levels are the first left out and its chapters the last."""

ENTRY_TOKENS = 8
"""What one carried line costs beyond its label: its id, its parent's id, the braces."""

CHAIN_DEPTH = 24
"""How far up the ancestry is walked to find the open chain. A guard against a cycle, not
a budget -- what fits is decided by tokens."""


@dataclass(frozen=True)
class Carried:
    """The outline so far, as much of it as fits, and where this page attaches to it.

    `units` is in reading order, `parents` says who holds whom inside it, `open` is the
    path from the document down to where the page before stopped, and `omitted` counts the
    subtrees dropped under a unit to make room.
    """

    units: tuple[HierarchyUnit, ...] = ()
    parents: dict[str, str] = field(default_factory=dict)
    open: tuple[str, ...] = ()
    omitted: dict[str, int] = field(default_factory=dict)


class Placements:
    """Where every unit read so far landed, kept as the pages are read.

    The page turn hands the next page the outline built up to here, so it is written down
    once as it is decided rather than rebuilt from every decision the document has made
    each time a page ends.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._children: dict[str, list[str]] = {}
        self.dropped: set[str] = set()
        """What a page dropped: never carried on, and never where a page stopped."""

    def record(self, decisions: Iterable[HierarchyDecision]) -> None:
        decisions = tuple(decisions)
        # Every drop first: a unit placed under one reads at the root, whatever the order.
        self.dropped |= dropped_subjects(decisions)
        for decision in decisions:
            if decision.status == "drop":
                continue
            parent = decision.parent_id
            if parent in self.dropped:
                continue  # placed under what was dropped: it reads at the root
            if decision.status != "parent" or parent is None:
                continue
            self._parent[decision.subject_id] = parent
            self._children.setdefault(parent, []).append(decision.subject_id)

    def carried(
        self, scope: HierarchyScopeUnits, seen: Mapping[str, HierarchyUnit]
    ) -> Carried:
        """The outline so far, cut to the budget, still whole where it is kept.

        Two things travel. The open chain -- the last thing this page decided and whatever
        it reads under, up to the document -- is where the next page attaches, so it is
        always here even where its own branches were dropped. Everything else travels as
        complete subtrees: a unit comes with all of its descendants or it does not come at
        all. A heading whose items were dropped says nothing the next page can use; it
        reads as a section that started there and holds nothing.

        What a picture holds is left out of the chain: a page often ends inside a figure,
        and its last unit is then a word printed on that figure -- carried forward, the
        innermost thing still open would be a label on a drawing nobody can see any more.

        The newest subtrees are kept first. A section decided two pages ago is where this
        page continues; one decided at the start of the document is not.
        """
        spine = self._open_chain(scope, seen)
        kept = dict.fromkeys(spine)
        room = CARRY_TOKEN_BUDGET - sum(self._cost(unit_id, seen) for unit_id in spine)

        # The document's headings, shallowest first: without them a long document's early
        # chapters dropped out of the reference altogether once whole recent sections
        # filled it, and a late page was read with no idea its middle chapters existed.
        shape = int(CARRY_TOKEN_BUDGET * SKELETON_SHARE)
        depth = {unit_id: self._depth(unit_id, seen) for unit_id in seen}
        for heading in sorted(
            (unit_id for unit_id in seen if self._children.get(unit_id)),
            key=lambda unit_id: depth[unit_id],
        ):
            parent = self._parent.get(heading)
            if heading in kept or (parent in seen and parent not in kept):
                continue  # already here, or its parent is not -- the shape stays connected
            cost = self._cost(heading, seen)
            if cost > min(shape, room):
                continue
            kept[heading] = None
            shape -= cost
            room -= cost

        order = {unit_id: index for index, unit_id in enumerate(seen)}
        for subtree in self._offered(spine, seen, order):
            new = [unit_id for unit_id in subtree if unit_id not in kept]
            cost = sum(self._cost(unit_id, seen) for unit_id in new)
            if cost <= room:
                kept.update(dict.fromkeys(new))
                room -= cost

        units = tuple(
            # Sent at the length its cost was counted at.
            seen[unit_id].model_copy(
                update={"scope": scope.scope, "label": self._label(unit_id, seen)}
            )
            for unit_id in sorted(kept, key=lambda unit_id: order.get(unit_id, 0))
        )
        return Carried(
            units=units,
            parents={
                unit_id: self._parent[unit_id]
                for unit_id in kept
                if self._parent.get(unit_id) in kept
            },
            open=spine,
            # What a carried unit holds and did not bring, so it does not read as a
            # section that began here and holds nothing.
            omitted={
                unit_id: missing
                for unit_id in kept
                if (
                    missing := sum(
                        1 for child in self._children.get(unit_id, ()) if child not in kept
                    )
                )
            },
        )

    def _depth(self, unit_id: str, seen: Mapping[str, HierarchyUnit]) -> int:
        depth, node = 0, self._parent.get(unit_id)
        while node is not None and node in seen and depth < CHAIN_DEPTH:
            depth, node = depth + 1, self._parent.get(node)
        return depth

    def _label(self, unit_id: str, seen: Mapping[str, HierarchyUnit]) -> str:
        limit = CARRY_LABEL_CHARS if self._children.get(unit_id) else CARRY_ITEM_CHARS
        return seen[unit_id].label[:limit]

    def _open_chain(
        self, scope: HierarchyScopeUnits, seen: Mapping[str, HierarchyUnit]
    ) -> tuple[str, ...]:
        """The path from the document down to where this page stopped, outermost first."""
        chain: list[str] = []  # innermost first, up to the document
        # Where the page stopped is its last unit that was kept: a page number below the text
        # is not what the next page continues.
        node_id: str | None = next(
            (unit.id for unit in reversed(scope.units) if unit.id in seen), None)
        while node_id is not None and node_id not in chain and len(chain) < CHAIN_DEPTH:
            chain.append(node_id)
            node_id = self._parent.get(node_id)

        def is_a_picture(unit_id: str) -> bool:
            unit = seen.get(unit_id)
            return unit is not None and unit.node_type in VISUAL_TYPES

        # A picture closes at the page break, and so does everything printed on it. Cutting
        # at the innermost one leaves the chain a reader would have: sections, not labels.
        drawn = [index for index, unit_id in enumerate(chain) if is_a_picture(unit_id)]
        still_open = chain[max(drawn) + 1 :] if drawn else chain
        return tuple(unit_id for unit_id in reversed(still_open) if unit_id in seen)

    def _offered(
        self,
        spine: Sequence[str],
        seen: Mapping[str, HierarchyUnit],
        order: Mapping[str, int],
    ) -> list[tuple[str, ...]]:
        """The complete subtrees that may travel beside the chain, newest first."""
        on_spine = set(spine)
        offered: list[tuple[str, ...]] = []
        for holder in (*spine, None):
            children = (
                self._children.get(holder, ())
                if holder is not None
                else [
                    unit_id
                    for unit_id in seen
                    if unit_id not in self._parent and unit_id not in on_spine
                ]
            )
            offered.extend(
                self._subtree(child)
                for child in children
                if child not in on_spine and child in seen
            )
        offered.sort(key=lambda subtree: order.get(subtree[0], 0), reverse=True)
        return offered

    def _subtree(self, root: str) -> tuple[str, ...]:
        """A unit and everything under it -- what travels together or not at all."""
        out: dict[str, None] = {}
        stack = [root]
        while stack:
            unit_id = stack.pop()
            if unit_id in out:
                continue
            out[unit_id] = None
            stack.extend(reversed(self._children.get(unit_id, ())))
        return tuple(out)

    def _cost(self, unit_id: str, seen: Mapping[str, HierarchyUnit]) -> int:
        label = self._label(unit_id, seen) if unit_id in seen else ""
        return ENTRY_TOKENS + estimated_tokens(label)

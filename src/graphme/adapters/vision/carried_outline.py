"""What a flowing document's page is shown of the document's headings before it.

The headings travel as the outline itself, not a chain, and all of them: a later page is
only read as well as what came before it is known, so nothing is left out to save tokens.
The open chain says where the page before stopped.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from graphme.application.hierarchy_decisions import dropped_subjects
from graphme.application.page_hierarchy_scope import HierarchyScopeUnits
from graphme.application.visual_crops import VISUAL_TYPES
from graphme.domain.models import HierarchyDecision, HierarchyUnit

CARRY_LABEL_CHARS = 80
"""A carried heading is there to be recognised, not read. Its opening words do that."""

CHAIN_DEPTH = 24
"""How far up the ancestry is walked to find the open chain. A guard against a cycle, not
a budget -- what fits is decided by tokens."""


@dataclass(frozen=True)
class Carried:
    """The outline so far and where this page attaches to it.

    `units` is in reading order, `parents` says who holds whom inside it, and `open` is the
    path from the document down to where the page before stopped.
    """

    units: tuple[HierarchyUnit, ...] = ()
    parents: dict[str, str] = field(default_factory=dict)
    open: tuple[str, ...] = ()


class Placements:
    """Where every unit read so far landed, kept as the pages are read.

    The page turn hands the next page the outline built up to here, so it is written down
    once as it is decided rather than rebuilt from every decision the document has made
    each time a page ends.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
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

    def carried(
        self, scope: HierarchyScopeUnits, seen: Mapping[str, HierarchyUnit]
    ) -> Carried:
        """The outline so far, whole, and the open chain -- the last thing this page decided
        and whatever it reads under, up to the document -- which is where the next page
        attaches.

        What a picture holds is left out of the chain: a page often ends inside a figure,
        and its last unit is then a word printed on that figure -- carried forward, the
        innermost thing still open would be a label on a drawing nobody can see any more.
        """
        return Carried(
            # Sent at its opening words.
            units=tuple(
                unit.model_copy(update={"scope": scope.scope,
                                        "label": unit.label[:CARRY_LABEL_CHARS]})
                for unit in seen.values()
            ),
            parents={unit_id: parent for unit_id, parent in self._parent.items()
                     if unit_id in seen and parent in seen},
            open=self._open_chain(scope, seen),
        )

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

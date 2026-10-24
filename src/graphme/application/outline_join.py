"""Joining a flowing document's pages into one outline, after each page was read on its own.

Every page is read by itself, side by side. What a page decides inside itself is kept; where
its top units -- those with nothing above them on the page -- go in the outline of the pages
before it is a small question of its own, one per page break, asked on text alone. A join
depends only on the page readings and the document's headings, never on another join, so
every page break is asked at once.

What a page is offered (`Joining.places`): the document's top, the sections open where the
page begins (walked up from where the page before stopped -- through that page's own reading,
then across pages by the headings), how the page before ended, and what the page before was
offered. What is open where a page begins was open where the page before began, or that page
opened it, so the headings' guess at the frame is checked against both.

A top answers a place and `under` or `beside`. Beside takes the neighbour's parent, which is
known only once every page is joined (`settle`).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from graphme.domain.enums import NodeType
from graphme.domain.models import (
    HeadingLine,
    HierarchyDecision,
    HierarchyUnit,
    JoinPlace,
    JoinPlacement,
    PageJoinRequest,
)

JOIN_READINGS = 2
"""Readings of each page break. Where they differ a third is asked, and the answer most
readings gave is kept."""

MAX_HEADING_LINES = 2500
"""Past this many text units the headings are not asked: one call would be too large."""

HOLDS_SHOWN = 3
"""How many of what a place holds are shown beside it: enough to say what kind of thing it
is -- a title holding a date and a version, a section holding paragraphs."""

How = Literal["under", "beside"]
Answer = tuple[How, str]


@dataclass(frozen=True)
class PageTree:
    """One page's own reading: the units it kept, in reading order, and each one's parent on
    the same page. A parent the reading named on another page is not kept -- that is the
    join's question."""

    index: int
    kept: tuple[str, ...]
    local: Mapping[str, str]
    dropped: frozenset[str]

    @classmethod
    def of(cls, index: int, units: Sequence[str],
           decisions: Iterable[HierarchyDecision]) -> PageTree:
        decided = {d.subject_id: d for d in decisions}
        dropped = frozenset(u for u, d in decided.items() if d.status == "drop")
        kept = tuple(u for u in units if u not in dropped)
        on_page = set(kept)
        local = {u: parent for u in kept
                 if (parent := decided[u].parent_id if u in decided else None) in on_page}
        return cls(index, kept, local, dropped)

    @property
    def tops(self) -> tuple[str, ...]:
        return tuple(u for u in self.kept if u not in self.local)


def headings(lines: Sequence[HeadingLine],
             answered: Iterable[tuple[str, str | None]]) -> tuple[HierarchyDecision, ...]:
    """The document's headings as decisions: each under the heading it names, when that one
    is a heading too and does not come after it; else at the top."""
    page = {line.unit_id: line.page for line in lines}
    answered = [(u, p) for u, p in answered if u in page]
    named = {u for u, _ in answered}
    return tuple(
        HierarchyDecision(subject_id=u, status="parent", parent_id=p, confidence=0.5,
                          rationale="headings")
        if p is not None and p in named and p != u and page[p] <= page[u]
        else HierarchyDecision(subject_id=u, status="root", confidence=0.5,
                               rationale="headings")
        for u, p in answered
    )


class Joining:
    """Every page's reading and the document's headings, with what the joins are asked."""

    def __init__(
        self,
        *,
        root: str,
        root_label: str,
        trees: Sequence[PageTree],
        units: Mapping[str, HierarchyUnit],
        order: Mapping[str, tuple[int, int]],
        heads: Sequence[HierarchyDecision],
    ) -> None:
        """`units` and `order` (page, place on it) cover every page asked, read or not."""
        self.root = root
        self._trees = {tree.index: tree for tree in trees}
        self._first = trees[0].index if trees else 0
        self._units = units
        self._label = {u: " ".join(unit.label.split()) for u, unit in units.items()}
        self._label[root] = " ".join(root_label.split())
        self._order = order
        self.dropped = frozenset(u for tree in trees for u in tree.dropped)
        self._local_up = {u: p for tree in trees for u, p in tree.local.items()}
        self._held: dict[str, list[str]] = {}
        for tree in trees:
            for u in tree.kept:
                if (parent := tree.local.get(u)) is not None:
                    self._held.setdefault(parent, []).append(u)
        self._up = {d.subject_id: d.parent_id for d in heads
                    if d.status == "parent" and d.parent_id}
        self._heads = sorted((d.subject_id for d in heads),
                             key=lambda u: self._order.get(u, (0, 0)))
        self.title = self._title()
        self.top = self.title or root

    def _title(self) -> str | None:
        """The first page's top that holds the most of it: its title."""
        first = self._trees.get(self._first)
        if first is None or not first.tops:
            return None

        def under(u: str) -> int:
            count = 0
            for k in first.kept:
                at = first.local.get(k)
                while at is not None:
                    if at == u:
                        count += 1
                        break
                    at = first.local.get(at)
            return count

        tops = first.tops
        return max(tops, key=lambda u: (under(u), -tops.index(u)))

    def textual(self, u: str) -> bool:
        """A table or a picture holds only its parts: never a section anything opens in."""
        unit = self._units.get(u)
        return unit is None or unit.node_type is NodeType.TEXT_CHUNK

    def frame(self, index: int) -> list[str]:
        """The sections open where page `index` begins, outermost first: from the last unit
        the pages before kept, up through its page's own reading, then across pages by the
        headings -- or, where the headings never placed a page's top, from the last heading
        before it, once: guessing again walks every page back."""
        last = next((self._trees[i].kept[-1] for i in sorted(self._trees, reverse=True)
                     if i < index and self._trees[i].kept), None)
        if last is None:
            return [self.top]
        path: list[str] = []
        at: str | None = last
        guessed = False
        while at is not None and at not in path and at not in (self.top, self.root):
            path.append(at)
            nxt = self._local_up.get(at, self._up.get(at))
            if nxt is None and not guessed:
                guessed = True
                before = self._order.get(at, (0, 0))
                earlier = [u for u in self._heads if self._order.get(u, (0, 0)) < before
                           and u not in self.dropped and u not in path]
                nxt = earlier[-1] if earlier else None
            at = nxt
        path.reverse()
        return [self.top, *[u for u in path if u != self.top and self.textual(u)]]

    def ending(self, index: int) -> list[str]:
        """How page `index` ended: its tops, each joined on its own and perhaps under an
        earlier one, then its last unit and what holds it on that page."""
        tree = self._trees.get(index)
        if tree is None or not tree.kept:
            return []
        chain: list[str] = []
        at: str | None = tree.kept[-1]
        while at is not None and at not in chain:
            chain.append(at)
            at = tree.local.get(at)
        heads = [u for u in tree.tops if u not in chain]
        return [*heads, *reversed(chain)]

    def places(self, index: int, *, back: bool = True) -> list[str]:
        """What page `index`'s tops may go under or beside, the document's top first."""
        tree = self._trees.get(index)
        tops = set(tree.tops) if tree is not None else set()
        near = [*self.frame(index), *self.ending(index - 1)]
        if back and index - 1 > self._first:
            near += self.places(index - 1, back=False)
        ordered = sorted(dict.fromkeys(near),
                         key=lambda u: (u != self.top, self._order.get(u, (0, 0))))
        kept = [u for u in ordered if u not in tops and u not in self.dropped
                and (u == self.top or self.textual(u))]
        return kept if self.top in kept else [self.top, *kept]

    def request(self, index: int) -> PageJoinRequest | None:
        """What page `index` is asked, or None when it has nothing to join."""
        tree = self._trees.get(index)
        if tree is None or not tree.tops or index == self._first:
            return None
        under: dict[str, list[str]] = {}
        for u in tree.kept:
            if (parent := tree.local.get(u)) is not None:
                under.setdefault(parent, []).append(u)

        def place(u: str, held: Sequence[str], indent: int | None = None) -> JoinPlace:
            return JoinPlace(unit_id=u, label=self._label.get(u, ""), indent=indent,
                             holds=tuple(self._label.get(k, "") for k in held[:HOLDS_SHOWN]),
                             held=len(held))

        return PageJoinRequest(
            page_index=index,
            places=tuple(place(u, self._held.get(u, ())) for u in self.places(index)),
            tops=tuple(place(u, under.get(u, ()), self._units[u].indent if u in self._units
                             else None) for u in tree.tops),
        )

    def vote(self, request: PageJoinRequest,
             readings: Sequence[Sequence[JoinPlacement]]) -> dict[str, Answer]:
        """Each top's answer: the one most readings gave; with no majority, the one opening
        fewer levels -- a wrong deep answer carries every later sibling down with it, a
        wrong shallow one flattens one level. A top no reading answered goes under the
        innermost section open where the page begins."""
        places = [p.unit_id for p in request.places]
        tops = [t.unit_id for t in request.tops]
        depth = {u: i for i, u in enumerate(places)}
        depth.update({u: len(places) + i for i, u in enumerate(tops)})
        valid = [self._valid(reading, places, tops) for reading in readings]
        chosen: dict[str, Answer] = {}
        for u in tops:
            options = [r[u] for r in valid if u in r]
            if options:
                chosen[u] = min(options, key=lambda o: (
                    -options.count(o), depth[o[1]] + (o[0] == "under"), o[0] == "under"))
            else:
                chosen[u] = ("under", self.frame(request.page_index)[-1])
        return chosen

    def disagree(self, request: PageJoinRequest,
                 readings: Sequence[Sequence[JoinPlacement]]) -> bool:
        places = [p.unit_id for p in request.places]
        tops = [t.unit_id for t in request.tops]
        valid = [self._valid(reading, places, tops) for reading in readings]
        return any(len({r.get(u) for r in valid}) > 1 for u in tops)

    def _valid(self, reading: Sequence[JoinPlacement], places: Sequence[str],
               tops: Sequence[str]) -> dict[str, Answer]:
        """A reading's answers that name a place offered, or an earlier top of the same page
        that holds text. Beside the document's top is under it: nothing stands beside it."""
        got: dict[str, Answer] = {}
        for answer in reading:
            u, at = answer.subject_id, answer.place_id
            if u not in tops:
                continue
            if at in places:
                got[u] = ("under" if at == places[0] else answer.how, at)
            elif at in tops[:tops.index(u)] and self.textual(at):
                got[u] = (answer.how, at)
        return got

    def settle(self, joined: Mapping[int, Mapping[str, Answer]]) -> tuple[HierarchyDecision, ...]:
        """Every unit's parent, the joins applied: under sets it, beside takes the
        neighbour's, resolved once every page is joined."""
        parent: dict[str, str] = dict(self._local_up)
        for index, tree in self._trees.items():
            for u in tree.tops:
                parent[u] = (self.root if (u == self.title or index != self._first)
                             else self.top)
        besides: dict[str, str] = {}
        for placed in joined.values():
            for u, (how, at) in placed.items():
                if how == "under":
                    parent[u] = at
                else:
                    besides[u] = at

        def resolve(u: str) -> str:
            seen: set[str] = set()
            while u in besides:
                if u in seen:
                    return self.top
                seen.add(u)
                u = besides[u]
            return parent.get(u, self.root)

        for u in besides:
            parent[u] = resolve(u)
        for u, p in parent.items():
            if p == u:
                parent[u] = self.top
        return (
            *(HierarchyDecision(subject_id=u, status="drop", confidence=0.9,
                                rationale="dropped on its page") for u in sorted(self.dropped)),
            *(HierarchyDecision(subject_id=u, status="root", confidence=0.9, rationale="joined")
              if p == self.root else
              HierarchyDecision(subject_id=u, status="parent", parent_id=p, confidence=0.9,
                                rationale="joined")
              for u, p in parent.items()),
        )

"""What a page of a flowing document is shown of the headings before it: all of them.

The carried outline is the reference a page reads its own units against, and a later page
is only read as well as what came before is known -- so none of it is left out to save
tokens. Invented headings.
"""
from __future__ import annotations

from graphme.adapters.vision.carried_outline import Placements as _Placements
from graphme.application.page_hierarchy_scope import HierarchyScopeUnits
from graphme.domain.enums import NodeType
from graphme.domain.models import HierarchyDecision, HierarchyUnit


def _unit(unit_id: str, label: str, order: int) -> HierarchyUnit:
    return HierarchyUnit(
        id=unit_id, label=label, scope="page:1", order=order, node_type=NodeType.TEXT_CHUNK
    )


def _decision(subject: str, parent: str | None) -> HierarchyDecision:
    return HierarchyDecision(
        subject_id=subject,
        status="parent" if parent else "root",
        parent_id=parent,
        confidence=0.9,
        rationale="Read.",
    )


def _document(sections: int, items: int) -> tuple[_Placements, dict[str, HierarchyUnit]]:
    """A title holding `sections` sections, each holding `items` lines."""
    placements = _Placements()
    seen: dict[str, HierarchyUnit] = {}
    order = 0

    def add(unit_id: str, label: str, parent: str | None) -> None:
        nonlocal order
        order += 1
        seen[unit_id] = _unit(unit_id, label, order)
        placements.record((_decision(unit_id, parent),))

    add("title", "표제 " + "가" * 20, None)
    for section in range(sections):
        head = f"s{section}"
        add(head, f"□ 절 {section} " + "나" * 20, "title")
        for item in range(items):
            add(f"{head}-{item}", f"- 항목 {section}.{item} " + "다" * 20, head)
    return placements, seen


def _scope(seen: dict[str, HierarchyUnit], last: str) -> HierarchyScopeUnits:
    return HierarchyScopeUnits(
        scope="page:2", page_index=2, scope_root_id="doc", units=(seen[last],)
    )


def test_a_long_outline_travels_whole() -> None:
    """Thirty sections of six items each: every heading and every item reaches the page.

    A budget once cut the deepest levels first, so a late page of a long document read its
    own subsection without the ones beside it."""
    placements, seen = _document(sections=30, items=6)
    carried = placements.carried(_scope(seen, "s29-5"), seen)

    assert [unit.id for unit in carried.units] == list(seen)
    assert carried.parents == {
        unit_id: ("title" if "-" not in unit_id else unit_id.split("-")[0])
        for unit_id in seen if unit_id != "title"
    }


def test_the_open_chain_is_where_the_page_attaches() -> None:
    placements, seen = _document(sections=20, items=5)
    carried = placements.carried(_scope(seen, "s19-4"), seen)

    assert carried.open == ("title", "s19", "s19-4")


def test_a_carried_heading_is_sent_at_its_opening_words() -> None:
    from graphme.adapters.vision.carried_outline import CARRY_LABEL_CHARS

    placements = _Placements()
    seen: dict[str, HierarchyUnit] = {}
    for index in range(3):
        unit_id = f"p{index}"
        seen[unit_id] = _unit(unit_id, f"Heading {index} " + "word " * 60, index + 1)
        placements.record((_decision(unit_id, None),))
    carried = placements.carried(_scope(seen, "p2"), seen)

    assert all(len(unit.label) == CARRY_LABEL_CHARS for unit in carried.units)


def test_what_a_page_dropped_is_never_where_the_next_page_continues() -> None:
    """A page number under the last paragraph was dropped: the next page continues the
    paragraph's section, and the page number is carried nowhere."""
    placements, seen = _document(sections=1, items=2)
    seen["pn"] = _unit("pn", "3", 99)
    placements.record((HierarchyDecision(subject_id="pn", status="drop", parent_id=None,
                                         confidence=0.9, rationale="Page number."),))
    kept = {k: v for k, v in seen.items() if k not in placements.dropped}
    scope = HierarchyScopeUnits(scope="page:2", page_index=2, scope_root_id="doc",
                                units=(seen["s0-1"], seen["pn"]))
    carried = placements.carried(scope, kept)
    assert carried.open == ("title", "s0", "s0-1")
    assert "pn" not in {unit.id for unit in carried.units}


def test_a_unit_under_one_the_same_answer_drops_reads_at_the_root_whatever_the_order() -> None:
    placements, seen = _document(sections=1, items=1)
    seen["g"] = _unit("g", "logo", 50)
    seen["x"] = _unit("x", "본문", 51)
    placements.record((_decision("x", "g"),
                       HierarchyDecision(subject_id="g", status="drop", parent_id=None,
                                         confidence=0.9, rationale="Logo.")))
    assert "g" in placements.dropped
    assert placements._parent.get("x") is None

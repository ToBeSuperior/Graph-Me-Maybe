"""How far a document's reading has come, counted in the questions each stage asks.

The page said "reading" for minutes with nothing to show how far: every stage knows how many
questions it will ask before it asks the first, so the reading is as far along as those
answered over all planned.
"""
from __future__ import annotations

from typing import Any

from graphme.adapters.vision.hierarchy_scope import RenderedHierarchyScopeProvider
from graphme.application.page_units import asked_together
from graphme.application.progress import Progress
from tests.public.test_pages_joined import PAGES, _Pages, _report, _Vision


def test_the_outline_plans_a_question_for_each_page_break_and_the_headings_and_ends_whole(
) -> None:
    progress = Progress()
    seen: list[int] = []

    class Watching(_Vision):
        def join_page(self, *, request: Any) -> Any:
            seen.append(progress.snapshot()["percent"])  # type: ignore[index]
            return super().join_page(request=request)

    RenderedHierarchyScopeProvider(renderer=_Pages(), vision=Watching(),
                                   progress=progress).derive(extracted=_report(),
                                                             source_path="r.pdf")
    pages = len(PAGES)
    said = progress.snapshot()
    assert said == {"stage": "outline", "done": 1 + pages + (pages - 1),
                    "total": 1 + pages + (pages - 1), "percent": 100}
    assert seen == sorted(seen) and seen[0] > 0


def test_questions_asked_side_by_side_are_counted_as_they_land() -> None:
    progress = Progress()
    answers = asked_together([1, 2, 3], lambda job: job * 2, workers=3, progress=progress,
                             stage="captions")
    assert answers == [2, 4, 6]
    assert progress.snapshot() == {"stage": "captions", "done": 3, "total": 3, "percent": 100}


def test_an_early_estimate_is_part_of_the_share_until_the_stage_counts_its_own() -> None:
    progress = Progress()
    progress.estimate(figures=4, outline=4)
    progress.plan("figures", 4)
    progress.step("figures", 4)
    assert progress.snapshot()["percent"] == 50  # type: ignore[index]
    progress.step("figures")  # never past what was planned
    assert progress.counts() == (4, 8)


def test_the_share_shown_never_moves_back_when_a_stage_counts_more_than_its_estimate() -> None:
    progress = Progress()
    progress.estimate(figures=4, captions=4)
    progress.plan("figures", 4)
    progress.step("figures", 4)
    before = progress.snapshot()["percent"]  # type: ignore[index]
    progress.plan("captions", 12)  # more visuals than pages
    assert progress.snapshot()["percent"] >= before  # type: ignore[index]

"""How far a document's reading has come, as the questions it asks the model.

Every stage knows how many questions it will ask before it asks the first: a page each for the
figures, the runs the paragraphs are grouped in, a page each and a page break each for the
outline, a page each for captions and for the documents a page names. A stage plans its count
when it starts and steps it as each answer lands, from whichever thread asked; the reading is
as far along as the questions answered over all planned. An estimate planned early is replaced
by the stage's own count when it starts; the share said never moves back for it -- it waits
until the questions answered pass it again.
"""
from __future__ import annotations

import threading
from typing import Any

STAGES = ("figures", "paragraphs", "outline", "captions", "references")


class Progress:
    """Each stage's questions planned and answered."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, list[int]] = {}
        self._stage: str | None = None
        self._shown = 0

    def plan(self, stage: str, total: int) -> None:
        """`stage` will ask `total` questions: its own count, replacing any estimate."""
        with self._lock:
            done = self._counts.get(stage, [0, 0])[0]
            self._counts[stage] = [min(done, total), max(0, total)]
            self._stage = stage

    def estimate(self, **totals: int) -> None:
        """Counts for stages that have not planned their own yet."""
        with self._lock:
            for stage, total in totals.items():
                self._counts.setdefault(stage, [0, max(0, total)])

    def step(self, stage: str, count: int = 1) -> None:
        """`count` of `stage`'s questions answered -- never more than it planned."""
        with self._lock:
            counted = self._counts.setdefault(stage, [0, 0])
            counted[0] = min(counted[0] + count, max(counted[1], counted[0]))
            self._stage = stage

    def snapshot(self) -> dict[str, Any] | None:
        """The stage under way, its count, and the share of every planned question answered;
        None before anything is planned."""
        with self._lock:
            total = sum(t for _, t in self._counts.values())
            if not total or self._stage is None:
                return None
            done = sum(d for d, _ in self._counts.values())
            stage_done, stage_total = self._counts[self._stage]
            self._shown = max(self._shown, min(100, 100 * done // total))
            return {"stage": self._stage, "done": stage_done, "total": stage_total,
                    "percent": self._shown}

    def counts(self) -> tuple[int, int]:
        """Questions answered and planned, over every stage."""
        with self._lock:
            return (sum(d for d, _ in self._counts.values()),
                    sum(t for _, t in self._counts.values()))

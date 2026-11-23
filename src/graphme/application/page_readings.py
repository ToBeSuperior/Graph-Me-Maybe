"""What the model said about a page, kept by what the page holds.

An edited document is a new file: its content hash, and with it every node id, changes. Most
of its pages do not. A page's reading is kept under a key made of what the page is asked
with -- each unit's text, kind and place, the stored crop of each, the page's size, and the
question -- so a page that did not change is not asked again, and one that did is.

A reading names units by id, and ids follow the whole file's content, so a reading is kept
with the ids it was given in the order the key lists them, and handed back in the ids that
stand in those places now. `READING_RULE` is part of every key: bump it whenever a prompt or
a rule changes what a page is answered, and every page is asked again.
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from graphme.application.reading_order import stated_box
from graphme.domain.ids import canonical_json, sha256_bytes
from graphme.domain.models import GraphNode
from graphme.files import write_atomic

READING_RULE = 1
"""The version of how a page is asked. Part of every key: changing it asks every page again."""

BUILDING_KEPT_SECONDS = 7 * 24 * 3600
"""How long the readings a build used are kept when it never put its document in the graph
(cancelled, failed, the app quit): a retry within it is not paid for again."""

T = TypeVar("T")


def page_key(
    units: Iterable[Sequence[Any]],
    crops: Iterable[str | None],
    size: Sequence[Any],
    rule: int | None = None,
    *,
    asked: str = "",
) -> str:
    """The key a page's reading is kept by: its units (text, kind, place), its crops' hashes,
    its size, the reading rule, and which question (`asked`) the reading answers."""
    said = [asked, READING_RULE if rule is None else rule, list(size),
            [list(unit) for unit in units], list(crops)]
    return sha256_bytes(canonical_json(said).encode("utf-8"))


def unit_entry(node: GraphNode) -> tuple[str, str, Any]:
    """What a unit puts in a page's key: its text, its kind, and where the file says it is."""
    where = stated_box(node.metadata) or node.locator.bbox or node.locator.cell_range
    return node.text or node.label, node.type.value, where


def crop_hash(crop: bytes | None) -> str | None:
    return None if crop is None else sha256_bytes(crop)


@dataclass(frozen=True)
class PageReading:
    """An answer as JSON, and the ids it was given in, in the order the key lists them."""

    names: tuple[str, ...]
    answer: Any

    def said(self, names: Sequence[str]) -> Any | None:
        """The answer in `names`, the ids standing in the same places now; None when the
        places do not line up."""
        if len(names) != len(self.names):
            return None
        return _renamed(self.answer, dict(zip(self.names, names, strict=True)))


def _renamed(value: Any, new: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return new.get(value, value)
    if isinstance(value, list):
        return [_renamed(item, new) for item in value]
    if isinstance(value, dict):
        return {new.get(k, k): _renamed(v, new) for k, v in value.items()}
    return value


class PageReadings:
    """Readings as `<key>.json` in a folder of the workspace, and, for each document the graph
    holds, `<content hash>.keys`: the readings its last build used, which the sweep keeps.

    `salt` is who reads -- provider, model, how many readings a page takes -- and goes into
    every file's name: another model's readings are never handed back as this one's."""

    def __init__(self, folder: Path, *, salt: str = "") -> None:
        self._folder = Path(folder)
        self._salt = salt

    def _file(self, key: str) -> str:
        return sha256_bytes(f"{self._salt}\0{key}".encode()) if self._salt else key

    def get(self, key: str) -> PageReading | None:
        try:
            data = json.loads(
                (self._folder / f"{self._file(key)}.json").read_text(encoding="utf-8"))
            return PageReading(names=tuple(data["names"]), answer=data["answer"])
        except (OSError, ValueError, KeyError, TypeError):
            return None  # none kept, or one that cannot be read: the page is asked

    def put(self, key: str, reading: PageReading) -> None:
        text = json.dumps({"names": list(reading.names), "answer": reading.answer},
                          ensure_ascii=False)
        write_atomic(self._folder / f"{self._file(key)}.json", text, suffix=".part", sync=False)

    def name(self, document: str, key: str) -> None:
        """The document of this content hash, being read, used the reading under `key`."""
        self._folder.mkdir(parents=True, exist_ok=True)
        with (self._folder / f"{document}.building").open("a", encoding="utf-8") as stream:
            stream.write(self._file(key) + "\n")

    def settle(self, read_as: str, held_as: str) -> None:
        """The document read as `read_as` is in the graph as `held_as` (a print is held as the
        file it was printed from): the readings this build used become its index, in place of
        the last build's."""
        building = self._folder / f"{read_as}.building"
        try:
            used = dict.fromkeys(building.read_text(encoding="utf-8").split())
        except OSError:
            return  # nothing was read through the readings
        write_atomic(self._folder / f"{held_as}.keys", "".join(f"{key}\n" for key in used),
                     suffix=".part", sync=False)
        building.unlink(missing_ok=True)

    def documents(self) -> set[str]:
        """The content hashes of every document a reading is kept for."""
        if not self._folder.is_dir():
            return set()
        return {path.stem for path in self._folder.glob("*.keys")}

    def sweep(self, documents: set[str], *, before: float) -> int:
        """Every reading no document in `documents` (content hashes the graph holds) names
        deleted, unless written since `before` -- a build under way -- or used by a build that
        never settled, within `BUILDING_KEPT_SECONDS`: how many."""
        if not self._folder.is_dir():
            return 0
        keep: set[str] = set()
        for index in self._folder.glob("*.keys"):
            if index.stem in documents:
                with contextlib.suppress(OSError):
                    keep.update(index.read_text(encoding="utf-8").split())
            elif _older(index, before):
                index.unlink(missing_ok=True)
        for building in self._folder.glob("*.building"):
            if _older(building, before - BUILDING_KEPT_SECONDS):
                building.unlink(missing_ok=True)
            else:
                with contextlib.suppress(OSError):
                    keep.update(building.read_text(encoding="utf-8").split())
        removed = 0
        for path in self._folder.glob("*.json"):
            if path.stem not in keep and _older(path, before):
                path.unlink(missing_ok=True)
                removed += 1
        # A write cut short.
        for path in self._folder.glob("*.part"):
            if _older(path, before):
                path.unlink(missing_ok=True)
        return removed


def _older(path: Path, before: float) -> bool:
    try:
        return path.stat().st_mtime < before
    except OSError:
        return False


def remembered(
    readings: PageReadings | None,
    key: str,
    names: Sequence[str],
    ask: Callable[[], T],
    *,
    load: Callable[[Any], T],
    dump: Callable[[T], Any],
    document: str,
) -> tuple[T, bool]:
    """The reading kept under `key`, in `names`, else `ask` and keep its answer: the answer,
    and whether it was kept. A kept answer `load` refuses is asked again; `dump` returning
    None keeps nothing (an answer standing in for a failed ask). What `ask` raises is not
    kept."""
    if readings is None:
        return ask(), False
    readings.name(document, key)
    found = readings.get(key)
    if found is not None and (said := found.said(names)) is not None:
        with contextlib.suppress(ValueError, TypeError, KeyError):
            return load(said), True
    answer = ask()
    if (kept := dump(answer)) is not None:
        readings.put(key, PageReading(names=tuple(names), answer=kept))
    return answer, False


def read_key(asked: str, page: Any, units: Sequence[GraphNode],
             crops: Mapping[str, bytes]) -> str:
    """The key of one question about a page's units: their entries, their stored crops, and
    the page's size."""
    return page_key([unit_entry(node) for node in units],
                    [crop_hash(crops.get(node.id)) for node in units],
                    (getattr(page, "width", None), getattr(page, "height", None)), asked=asked)

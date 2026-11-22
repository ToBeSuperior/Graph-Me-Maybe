"""Automatic builds on the targets the user chose, and on nothing else.

A target is a folder of a workspace's (`folder`), a folder of a connected cloud drive's desktop
app (`drive`) or a mail account (`mail`). A folder target is looked at every `WATCH_SECONDS`:
each document's path, size and modification time are compared with what was there when it was
chosen or last built -- no file is opened, so a drive's online-only file is not downloaded.
What changed is counted, and built as its mode says: `manual` never by itself, `on_change` once
nothing has changed for `SETTLE_MINUTES`, `daily` at its `at` (HH:MM, this machine's time). A
build is the normal build of the changed files, into the target's workspace while it is open
and no build runs; otherwise it waits for the next look. A file gone is listed as missing and
left in the graph: deleting a document is the user's decision. A mail account's mode is kept on
the account (`MailAccounts.set_mode`) and followed by `MailSync`.

The folder targets are `watch.json` in the app's home.
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from graphme.adapters.sources.desktop_drives import DESKTOP_DRIVES
from graphme.application.ingest import documents_at
from graphme.server.jobs import BUILDING, Busy, CorpusBuild

if TYPE_CHECKING:
    from collections.abc import Callable

    from graphme.config import Settings
    from graphme.server.state import Demo

WATCH_SECONDS = 300
SETTLE_MINUTES = 5
KINDS = ("folder", "drive", "mail")
MODES = ("manual", "on_change", "daily")
MAIL_BEFORE = "on_change"
"""The mode of a mail account connected before accounts had one: it fetched by itself."""
MAIL_NEW = "manual"

Seen = dict[str, tuple[int, float]]
NOT_OPEN = "its workspace is not open -- open it to build"
RUNNING = "a graph build is already running"
UNCHANGED = "nothing changed since the last build"
SYSTEM = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/dev", "/opt", "/Applications",
          "/proc", "/sys", "/boot", "C:/Windows", "C:/Program Files", "C:/Program Files (x86)",
          "C:/ProgramData")
"""Folders of the operating system's own: never a target, nor anything in them."""
PARENTS = ("/Users", "/home", "C:/Users")
"""Folders holding every user's home: never a target themselves (a home's folders can be)."""


@dataclass
class WatchTarget:
    kind: str
    id: str
    workspace: str
    mode: str = "manual"
    at: str | None = None
    path: str | None = None
    seen: dict[str, list[float]] = field(default_factory=dict)
    """Each document's [size, mtime] when the target was chosen or last built."""
    ran: str | None = None
    """When a daily target last took its time (ISO), built or with nothing to build."""


def home_of(settings: Settings) -> Path:
    """The app's home (`settings.home_of`), imported late: the settings module stands above
    the state this one is part of."""
    from graphme.server.settings import home_of as app_home
    return app_home(settings)


def next_daily(at: str, after: datetime) -> datetime:
    """The first `at` (HH:MM) later than `after`."""
    hour, minute = (int(part) for part in at.split(":"))
    due = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due if due > after else due + timedelta(days=1)


def daily_due(at: str | None, ran: str | None, now: datetime) -> bool:
    """Whether a daily `at` came since `ran` (never: due at once)."""
    last = _local(ran)
    return not at or last is None or now >= next_daily(at, last)


def _local(when: str | None) -> datetime | None:
    """An ISO time as this machine's clock reads it; a mail account's carries its offset."""
    if not when:
        return None
    said = datetime.fromisoformat(when)
    return said.astimezone().replace(tzinfo=None) if said.tzinfo is not None else said


def valid_at(at: str | None) -> bool:
    try:
        hour, minute = (int(part) for part in (at or "").split(":"))
    except ValueError:
        return False
    return 0 <= hour < 24 and 0 <= minute < 60


def scan(folder: Path, skipped: list[Path]) -> Seen:
    """Every document under `folder` by its size and modification time: stat only. A folder
    that did not list in time goes in `skipped`. OSError: `folder` cannot be read."""
    found: Seen = {}
    for path in documents_at(folder, skipped=skipped):
        try:
            stat = path.stat()
        except OSError:
            continue  # gone since it was listed
        found[str(path)] = (stat.st_size, stat.st_mtime)
    if folder in skipped:
        raise OSError(f"{folder} did not list")
    return found


@dataclass
class _Walk:
    """What one look at a target's folder found: its documents, the folders under it that did
    not list (what they hold is unknown, not missing), or why the folder could not be read --
    and the files its workspace's graph holds (None: not asked now)."""
    found: Seen = field(default_factory=dict)
    skipped: list[Path] = field(default_factory=list)
    error: str | None = None
    in_graph: dict[str, str] | None = None


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # a link that loops: kept as written
        return path


def _walk(target: WatchTarget) -> _Walk:
    walk = _Walk()
    try:
        walk.found = scan(Path(target.path or ""), walk.skipped)
    except OSError as error:
        walk.error = str(error) or type(error).__name__
    return walk


class Watcher:
    """The chosen targets, looked at every `WATCH_SECONDS` on one thread. A folder is walked
    outside the lock, so a slow drive holds up neither the page nor a choice."""

    def __init__(self, demo: Demo, *, now: Callable[[], datetime] = datetime.now) -> None:
        self._demo = demo
        self._now = now
        self._file = demo.workspace_registry().home / "watch.json"
        self._lock = threading.RLock()
        self._changed: dict[str, list[str]] = {}
        self._missing: dict[str, list[str]] = {}
        self._errors: dict[str, str] = {}
        """target -> why its folder cannot be read now."""
        self._scanned: dict[str, Seen] = {}
        """target -> what its folder held at the last look."""
        self._quiet_since: dict[str, tuple[frozenset[str], datetime]] = {}
        """target -> what was changed at the last look, and since when it has stayed so."""
        self._started: dict[str, tuple[CorpusBuild, Seen, frozenset[str]]] = {}
        """target -> the build started for it, what the folder held then and what changed."""
        self._failed: dict[str, frozenset[str]] = {}
        """target -> what had changed when its last build did not finish: not built again
        until something else changes."""

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 -- the next look tries again
                traceback.print_exc(file=sys.stderr)
            stop.wait(WATCH_SECONDS)

    # The targets --------------------------------------------------------------------------

    def targets(self) -> list[dict[str, Any]]:
        """Every chosen folder and drive target and every mail account, as the page shows them."""
        now = self._now()
        said = []
        graphs: dict[str, dict[str, str] | None] = {}
        for target in self._read():
            if target.workspace not in graphs:
                graphs[target.workspace] = self._graph_paths(target.workspace)
            in_graph = graphs[target.workspace]
            changed = self._changed.get(target.id, [])
            missing = [{"path": p, "document_id": in_graph.get(p) if in_graph else None}
                       for p in self._missing.get(target.id, [])
                       if in_graph is None or p in in_graph]
            started = self._started.get(target.id)
            said.append({
                "kind": target.kind, "id": target.id, "workspace": target.workspace,
                "mode": target.mode, "at": target.at, "path": target.path,
                "name": Path(target.path or "").name or target.path or target.id,
                "changed": len(changed), "missing": missing,
                "next_build": self._next(target, changed, now),
                "building": started is not None and started[0].state in BUILDING,
                "error": self._errors.get(target.id)})
        if self._demo.workspaces is not None:
            build = self._demo.build
            for item in self._demo.mail_accounts().listed():
                mode = str(item.get("mode") or MAIL_BEFORE)
                at = item.get("at")
                due = None
                if mode == "daily" and at:
                    due = next_daily(at, _local(item.get("last_checked")) or now)
                    if due <= now:
                        due = next_daily(at, now)
                said.append({
                    "kind": "mail", "id": item["id"], "workspace": item["workspace"],
                    "mode": mode, "at": at, "path": None, "name": item["address"],
                    "changed": len(item.get("pending", [])), "missing": [],
                    "next_build": due.isoformat() if due else None,
                    "building": build.state in BUILDING and build.source == item["address"],
                    "error": item.get("error")})
        return said

    def folders(self, workspace: str | None = None) -> list[dict[str, str]]:
        """The folders `workspace` (the open one) was built from, each a target that can be
        chosen: the folder each document of its graph is in, as the file system resolves it,
        uploads and folders never watched aside. Nested ones are offered too; one inside or
        around a target already chosen is refused when chosen."""
        spaces = self._demo.workspace_registry()
        workspace = workspace or spaces.active
        if spaces.item(workspace)["kind"] == "mail":
            return []
        held = self._demo.root.resolve()
        offered = []
        for folder in sorted({Path(p).parent for p in self._graph_paths(workspace) or {}}):
            if folder.is_relative_to(held) or not folder.is_dir() or self._unwatchable(folder):
                continue
            offered.append({"path": str(folder),
                            "kind": "drive" if self._in_drive(folder) else "folder"})
        return offered

    def _graph_paths(self, workspace: str) -> dict[str, str] | None:
        """Each file `workspace`'s graph was read from, and its document; None when the graph
        cannot be asked now. Never under the watcher's lock: it asks the graph DB."""
        try:
            ids = self._demo.reader(workspace).graph_space().source_paths()
        except Exception:  # noqa: BLE001 -- the graph starting or away: nothing is pruned
            return None
        # Resolved, as a target's folder is: a graph built through a link names the same files.
        return {str(_resolved(Path(path))): doc for doc, path in ids.items()}

    def choose(self, kind: str, id: str, mode: str, at: str | None,
               path: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        """A target chosen, or its mode changed. ValueError for one that cannot be."""
        if mode not in MODES:
            raise ValueError(f"a mode is one of {', '.join(MODES)}")
        if mode == "daily" and not valid_at(at):
            raise ValueError("a daily build needs its time, HH:MM")
        at = at if mode == "daily" else None
        if kind == "mail":
            self._demo.mail_accounts().set_mode(id, mode, at)
        else:
            self._choose_folder(kind, id, mode, at, path, workspace)
        return next(t for t in self.targets() if t["kind"] == kind and t["id"] == id)

    def _choose_folder(self, kind: str, id: str, mode: str, at: str | None,
                       path: str | None, workspace: str | None) -> None:
        with self._lock:
            targets = self._read()
            target = next((t for t in targets if t.id == id), None)
            if target is not None and target.kind != kind:
                raise ValueError(f"{id} is a {target.kind} target")
            if target is None:
                target = self._new(kind, id, path, workspace, targets)
                targets.append(target)
            target.mode, target.at = mode, at
            target.ran = self._now().isoformat()  # a daily time from now on, not one past
            self._write(targets)
            self._quiet_since.pop(id, None)

    def _new(self, kind: str, id: str, path: str | None, workspace: str | None,
             targets: list[WatchTarget]) -> WatchTarget:
        if kind not in ("folder", "drive"):
            raise ValueError(f"a target is one of {', '.join(KINDS)}")
        if not path:
            raise ValueError("a folder target needs its folder")
        spaces = self._demo.workspace_registry()
        workspace = workspace or spaces.active
        if spaces.item(workspace)["kind"] == "mail":
            raise ValueError("a mail workspace reads its own mailbox only")
        where = Path(path).expanduser().resolve()
        self._refuse(where, targets)
        if kind == "drive" and not self._in_drive(where):
            raise ValueError(f"{where} is not in a connected cloud drive")
        if str(where) not in {f["path"] for f in self.folders(workspace)}:
            raise ValueError(f"{where} is not a folder this workspace was built from -- build "
                             "it first, then choose it here")
        # What is there now is the start: only what changes from here is built.
        seen = {p: [size, mtime] for p, (size, mtime) in scan(where, []).items()}
        return WatchTarget(kind, id, workspace, path=str(where), seen=seen)

    def _refuse(self, where: Path, targets: list[WatchTarget]) -> None:
        """ValueError for a folder never watched (`_unwatchable`) or one inside or around a
        target already chosen."""
        if not where.is_dir():
            raise ValueError(f"not a folder: {where}")
        if why := self._unwatchable(where):
            raise ValueError(why)
        for target in targets:
            chosen = Path(target.path or "")
            if where.is_relative_to(chosen) or chosen.is_relative_to(where):
                raise ValueError(f"{where} is inside or around {chosen}, chosen already")

    def _unwatchable(self, where: Path) -> str | None:
        """Why a folder is never watched -- a whole drive, the home folder itself, the system's
        or the app's own folders -- or None."""
        if where == Path(where.anchor):
            return "a whole drive cannot be watched -- choose a folder on it"
        if where == Path.home().resolve():
            return "your home folder as a whole cannot be watched -- choose a folder in it"
        if any(where.is_relative_to(Path(s).resolve()) for s in SYSTEM) \
                or any(where == Path(s).resolve() for s in PARENTS):
            return "a system folder cannot be watched"
        if where.is_relative_to(home_of(self._demo.settings).resolve()):
            return "the app's own folder cannot be watched"
        return None

    def _in_drive(self, where: Path) -> bool:
        for kind in DESKTOP_DRIVES:
            drive = self._demo.desktop_drive(kind)
            if drive is not None and self._demo.drive_connected(kind) \
                    and where.is_relative_to(drive.base.resolve()):
                return True
        return False

    def _find(self, kind: str, id: str) -> WatchTarget:
        target = next((t for t in self._read() if t.id == id and t.kind == kind), None)
        if target is None:
            raise LookupError(f"no such {kind} target: {id}")
        return target

    def forget(self, kind: str, id: str) -> None:
        """A folder target no longer chosen: never looked at again."""
        with self._lock:
            self._find(kind, id)
            self._write([t for t in self._read() if t.id != id])
            self._changed.pop(id, None)
            self._missing.pop(id, None)
            self._errors.pop(id, None)
            self._scanned.pop(id, None)
            self._quiet_since.pop(id, None)
            self._started.pop(id, None)
            self._failed.pop(id, None)

    def build_now(self, kind: str, id: str) -> CorpusBuild:
        """A target built at once, whatever its mode: its changed files, or a mail account's
        new mail fetched and built. Busy, saying why, when it cannot be now."""
        if kind == "mail":
            item = self._demo.mail_accounts().get(id)
            since = item.get("read_since")
            return self._demo.build_mail(id, date.fromisoformat(since) if since else None)
        walk = self._look(self._find(kind, id))
        with self._lock:
            target = self._find(kind, id)
            self._take(target, walk)
            if error := self._errors.get(id):
                raise Busy(f"its folder cannot be read: {error}")
            if why := self._why_not(target):
                raise Busy(why)
            return self._start(target)

    # The looks ----------------------------------------------------------------------------

    def tick(self) -> None:
        """One look at every chosen folder target; a build started for each that is due."""
        for listed in self._read():
            walk = self._look(listed)  # outside the lock: a drive can take minutes to list
            with self._lock:
                target = next((t for t in self._read() if t.id == listed.id), None)
                if target is None:
                    continue  # forgotten meanwhile
                try:
                    self._take(target, walk)
                    if self._due(target) and self._why_not(target) is None:
                        self._start(target)
                except Busy:
                    pass  # a build began, or the workspace was switched: the next look
                except Exception:  # noqa: BLE001 -- one target's trouble stops no other
                    traceback.print_exc(file=sys.stderr)

    def _look(self, target: WatchTarget) -> _Walk:
        """The target's folder walked, and its graph's files asked for: no lock held."""
        walk = _walk(target)
        if walk.error is None:
            walk.in_graph = self._graph_paths(target.workspace)
        return walk

    def _take(self, target: WatchTarget, walk: _Walk) -> None:
        """What a walk found, set against what the target last built."""
        if walk.error is not None:
            if target.id not in self._errors:  # said once, when it begins
                print(f"graphme: {target.path} cannot be read: {walk.error}", file=sys.stderr)
            self._errors[target.id] = walk.error
            return
        if self._errors.pop(target.id, None) is not None:
            self._quiet_since.pop(target.id, None)  # back: it settles again before building
        if started := self._started.get(target.id):
            build, seen, key = started
            if build.state in BUILDING:
                return  # what changes meanwhile is the next look's
            del self._started[target.id]
            if build.state == "done":  # taken: what the folder held then is the new start
                gone = {p: v for p, v in target.seen.items() if p not in seen}
                target.seen = {**gone, **{p: [s, m] for p, (s, m) in seen.items()}}
                self._failed.pop(target.id, None)
                self._save(target)
            else:
                self._failed[target.id] = key
        found = walk.found

        def unknown(path: str) -> bool:
            return any(Path(path).is_relative_to(s) for s in walk.skipped)

        changed = sorted(p for p, (size, mtime) in found.items()
                         if target.seen.get(p) != [size, mtime])
        missing = sorted(p for p in set(target.seen) - set(found) if not unknown(p))
        in_graph = walk.in_graph
        if in_graph is not None and any(p not in in_graph for p in missing):
            # Gone from the folder and from the graph: nothing is left to say about it.
            target.seen = {p: v for p, v in target.seen.items()
                           if p in found or p in in_graph or unknown(p)}
            missing = [p for p in missing if p in in_graph]
            self._save(target)
        self._changed[target.id] = changed
        self._missing[target.id] = missing
        self._scanned[target.id] = found
        key = self._key(target.id)
        if self._quiet_since.get(target.id, (None,))[0] != key:
            self._quiet_since[target.id] = (key, self._now())

    def _key(self, id: str) -> frozenset[str]:
        found = self._scanned.get(id, {})
        return frozenset(f"{p}:{found[p]}" for p in self._changed.get(id, []))

    def _due(self, target: WatchTarget) -> bool:
        if target.id in self._started or target.id in self._errors:
            return False
        now = self._now()
        if target.mode == "on_change":
            key, since = self._quiet_since[target.id]
            return bool(self._changed.get(target.id)) and key != self._failed.get(target.id) \
                and now - since >= timedelta(minutes=SETTLE_MINUTES)
        if target.mode == "daily" and daily_due(target.at, target.ran, now):
            if not self._changed.get(target.id):
                target.ran = now.isoformat()  # nothing to build: the next day's time
                self._save(target)
                return False
            return True
        return False

    def _why_not(self, target: WatchTarget) -> str | None:
        """Why the target cannot be built now; None when it can."""
        if not self._changed.get(target.id):
            return UNCHANGED
        if self._demo.build.state in BUILDING:
            return RUNNING
        if self._demo.workspace_registry().active != target.workspace:
            return NOT_OPEN
        return None

    def _start(self, target: WatchTarget) -> CorpusBuild:
        """The changed files built into the target's workspace -- checked again as the build
        takes its slot, so a switch in between builds nothing (Busy)."""
        demo = self._demo
        changed = self._changed[target.id]
        build = demo.build_corpus([demo.open_path(p) for p in changed], target.path or "",
                                  into=target.workspace)
        self._started[target.id] = (build, dict(self._scanned[target.id]), self._key(target.id))
        if target.mode == "daily":
            target.ran = self._now().isoformat()
            self._save(target)
        return build

    def _next(self, target: WatchTarget, changed: list[str], now: datetime) -> str | None:
        if target.mode == "daily" and target.at:
            due = next_daily(target.at, _local(target.ran) or now)
            if due <= now:  # due and waiting (its workspace closed): the next time to come
                due = next_daily(target.at, now)
            return due.isoformat()
        if target.mode == "on_change" and changed and target.id in self._quiet_since:
            return (self._quiet_since[target.id][1]
                    + timedelta(minutes=SETTLE_MINUTES)).isoformat()
        return None

    # The file -----------------------------------------------------------------------------

    def _save(self, target: WatchTarget) -> None:
        self._write([target if t.id == target.id else t for t in self._read()])

    def _read(self) -> list[WatchTarget]:
        try:
            said = json.loads(self._file.read_text(encoding="utf-8"))
            return [WatchTarget(**t) for t in said["targets"]]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def _write(self, targets: list[WatchTarget]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        kept = self._file.with_suffix(".tmp")
        kept.write_text(json.dumps({"targets": [asdict(t) for t in targets]},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        kept.replace(self._file)

"""The graph space the server holds open, the workspaces it is switched between and read
beside, and the assets swept when nothing names them."""
from __future__ import annotations

import contextlib
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from graphme.adapters.embedding import sweep_vectors
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.adapters.storage.graph_rag import PASSAGE_CHARS, GraphRag
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.adapters.storage.workspace_scope import WorkspaceScope
from graphme.application.ingest import documents_at
from graphme.application.page_readings import PageReadings
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.server import state
from graphme.server.demo_base import DemoBase
from graphme.server.jobs import (
    BUILDING,
    EXPORTING,
    STARTING,
    Busy,
    CorpusBuild,
    ExportJob,
    GraphStarting,
)

MAX_SCOPE_FILES = 500
"""The most files a scope is found for: each is read whole to know its content, and a file kept
online only is downloaded for that."""


class TooManyFiles(Exception):
    """A scope's paths hold more than `MAX_SCOPE_FILES` files: turned away before any is read."""

    def __init__(self) -> None:
        super().__init__(f"these paths hold more than {MAX_SCOPE_FILES} files -- choose fewer "
                         "files, or ask in the workspace a folder already built is in")


class SpaceJobs(DemoBase):
    """The graph space, its workspaces, and the sweep of what nothing names."""

    def _wait_for_embed(self) -> None:
        waited = time.monotonic() + state.EMBED_WAIT_SECONDS
        while (self.build.kind == "embed" and self.build.state in BUILDING
               and time.monotonic() < waited):
            time.sleep(0.1)

    def switch_workspace(self, workspace: str, *, take_mail: bool = True) -> None:
        """The app moved onto `workspace`: the open graph's connection held to its scope, no
        Neo4j started or stopped. A mail workspace's held mail is taken behind unless not
        `take_mail`."""
        spaces = self.workspace_registry()
        spaces.item(workspace)
        if workspace == spaces.active:
            return
        # Opening a workspace embeds what it holds with the chosen search model, a second or
        # two: waited out, not refused as though a build ran. An embedding is never stopped
        # half way, so a long one still refuses once EMBED_WAIT_SECONDS have gone.
        # ponytail: polled, an event on the job if a wait ever needs to be exact
        self._wait_for_embed()
        # A graph not moved yet is moved before it is opened: minutes, said rather than waited.
        self.space(wait=state.SPACE_WAIT)
        with self.building:
            busy = any(s.being_read for s in self.sessions.values())
            if self.build.state in BUILDING or self.export.state in EXPORTING or busy:
                raise Busy("a build, a reading or an export is running -- switch once it ends")
            settings = state._in_workspace(self.settings, spaces, workspace)
            with self.opening_space:
                self._opened_space()  # opened during a build: its moves are made now
                self.graph = self.graph.held_to(settings, take=True)
                self.settings = settings
                self._forget_workspace()
            spaces.activate(workspace)
        self.search_models.embed_behind()  # a search model chosen while it was closed
        self.sweep_behind()
        if take_mail and spaces.item(workspace)["account"] is not None:
            self.mail_sync.opened(workspace)  # mail that came while it was closed

    def reader(self, workspace: str | None) -> state.GraphMe:
        """The graph to read `workspace` from: the open one's for None or the open one, else
        its scope over the open graph's connection -- kept while that is the open graph's,
        nothing started. LookupError: none."""
        spaces = self.workspaces
        if workspace is None or spaces is None or workspace == spaces.active:
            return self.graph
        spaces.item(workspace)
        self.space(wait=state.SPACE_WAIT)
        with self.opening_space:  # the graph opened, or the switch under way finished
            graph = self.graph
            base = self._opened_space()
        with self.siding:
            side = self._sides.get(workspace)
            if side is None or side[0] is not base:
                side = base, graph.held_to(state._in_workspace(self.settings, spaces, workspace))
                self._sides[workspace] = side
            return side[1]

    def delete_workspace(self, workspace: str) -> None:
        """`workspace` and its graph deleted -- its nodes, passages, constraints and indexes
        taken out of the one Neo4j, then its folder; never the one open."""
        spaces = self.workspace_registry()
        space = self.space(wait=state.SPACE_WAIT)
        with self.building:  # as a switch holds it: never the one a switch is opening
            spaces.refuse_open(workspace)
            mail = spaces.item(workspace)["account"]
            with self.siding:
                self._sides.pop(workspace, None)
            space.drop_workspace(WorkspaceScope(workspace))
        spaces.delete(workspace)
        if mail is not None:  # a mail workspace takes its account's password and store with it
            with contextlib.suppress(LookupError):
                self.mail_accounts().forget(mail)

    def sweep_assets(self) -> tuple[int, int]:
        """The crops and pictures nothing names any more deleted from the open workspace's
        folder -- a document deleted or read again, the graph reset, a reading long gone:
        how many, and how many bytes. The page readings no document in the graph names go
        with them. Kept: what the graph's nodes name, what a reading this
        run still holds (one outside the graph is put in again at no cost), and whatever was
        written in the last `SWEEP_GRACE` seconds. Not while a build or a reading runs."""
        with self.building:
            if self.build.state in BUILDING or any(s.being_read for s in self.sessions.values()):
                return 0, 0
            started = time.time()
            # the workspace's graph and its folders taken together: a switch after this
            # sweeps the next one on its own turn
            space, settings = self.graph.opened_space, self.settings
        if space is None:
            return 0, 0  # swept once it is opened
        keep = space.crop_hashes()
        for session in list(self.sessions.values()):
            if session.extracted is not None:
                keep |= {h for n in session.extracted.nodes
                         if isinstance(h := n.metadata.get(CROP_ASSET_KEY), str)}
            if session.kept is not None:
                keep |= set(session.kept.crops.values())
        removed, size = LocalAssetStore(settings.asset_dir).sweep(
            keep, before=started - state.SWEEP_GRACE)
        # What each page was answered, kept for the documents the graph holds: an edited one
        # is read again only where it changed.
        readings = PageReadings(settings.readings_dir)
        readings.sweep(space.contents(sorted(readings.documents())),
                       before=started - state.SWEEP_GRACE)
        # And the passages' vectors kept by their text: those of the passages the graph holds.
        sweep_vectors(settings.readings_dir / "passages",
                      model=settings.embedding_model,
                      texts=(text[:PASSAGE_CHARS] for text in space.passage_texts()),
                      before=started - state.SWEEP_GRACE)
        for folder in [f for f in self.root.iterdir() if f.is_dir()]:
            if folder.name not in self.sessions:  # a reading's folder, left empty
                with contextlib.suppress(OSError):
                    folder.rmdir()
        if removed:
            state._log.info("let go of %s unused crops (%.1f MB)", removed, size / 1e6)
        return removed, size

    def sweep_behind(self) -> None:
        threading.Thread(target=self._sweep_quietly, daemon=True).start()

    def _sweep_quietly(self) -> None:
        try:
            self.sweep_assets()
        except Exception:  # noqa: BLE001 -- disk kept a while longer; the next sweep tries again
            traceback.print_exc(file=sys.stderr)

    def _let_go_of_put(self, docs: list[str]) -> None:
        """The documents this build put in the graph held as the graph keeps them -- where each
        node is drawn -- not as read: a folder of thousands is not kept in memory whole."""
        held = {doc: s for doc in docs if (s := self.sessions.get(doc)) is not None
                and s.state == "done" and s.extracted is not None}
        if not held:
            return
        space = self.space()
        inside = space.files([s.extracted.document.id for s in held.values()
                              if s.extracted is not None])
        for doc, session in held.items():
            document_id = session.extracted.document.id if session.extracted else ""
            if document_id not in inside:
                continue  # outside the graph: kept to be put in again at no cost
            found = state.kept_from(space.document_graph(document_id), session.format)
            with self.opening, session.lock:
                if found is None:  # drawn again from the file when it is next looked at
                    self.sessions.pop(doc, None)
                    continue
                session.kept, session.pages = found
                session.extracted, session.state = None, "rendered"

    def _forget_workspace(self) -> None:
        """What this start knew of the graph just left: its jobs, readings, files and pages."""
        self.jobs.clear()
        self.build, self.export = CorpusBuild(), ExportJob()
        self.sessions.clear()
        self.catalog.clear()
        self.hashes.clear()
        self.previews.clear()
        self.putting = set()
        self.origins.clear()
        self._opener, self._open_failed = None, None
        self.dump_path.unlink(missing_ok=True)

    def space(self, *, wait: float = -1) -> Neo4jGraphSpace:
        """The graph space every built document is put in, opened on first use. A build waits
        for it however long; a page's request (`wait`) only so long."""
        # The first use may start the local Neo4j; the page asks for the list and the map at once.
        if wait < 0:
            with self.opening_space:
                return self._opened_space()
        if (opened := self.graph.opened_space) is not None and not self._moving:
            return opened
        # Fetching and starting Neo4j takes minutes, so a page's request never does it: it is done
        # behind, and the request told to wait (503, Retry-After) while it goes on.
        with self._starting_opener:  # the list and the map ask at once: one opening
            opener = self._opener
            if opener is None or not opener.is_alive():
                if (failed := self._open_failed) is not None:
                    self._open_failed = None
                    raise failed
                opener = self._opener = threading.Thread(target=self._open_behind, daemon=True)
                opener.start()
        opener.join(wait)
        if opener.is_alive():
            raise GraphStarting(STARTING)
        # Every request that joined a failed opening is told; a later one starts another.
        if (failed := self._open_failed) is not None:
            raise failed
        if (space := self.graph.opened_space) is None:
            raise GraphStarting(STARTING)
        return space

    def _opened_space(self) -> Neo4jGraphSpace:
        """The open graph's space, opened if need be; held under `opening_space`. The first
        time, with no build running, the workspaces whose graph is still in their own folder
        are moved into the one Neo4j first (`_move_workspaces`): a build, a switch or a page
        asking meanwhile waits, or is told the graph is starting."""
        space = self.graph.graph_space()
        if not self._moves_tried and self.build.state not in BUILDING:
            self._moves_tried, self._moving = True, True
            try:
                self._move_workspaces(space)
            finally:
                self._moving = False
        return space

    def _move_workspaces(self, space: Neo4jGraphSpace) -> None:
        """Each workspace whose graph is in its own folder (`neo4j-data/`, from before every
        workspace was in the one Neo4j) and not moved yet: that folder's Neo4j started once,
        its graph copied in under the workspace's scope and counted, the workspace marked, the
        Neo4j stopped. The folder is left as it is, for the owner to remove once checked. A
        move that fails leaves the workspace unmarked -- tried again at the next start -- and
        is said in `unmoved` (GET /v1/workspaces)."""
        spaces = self.workspaces
        if spaces is None:
            return
        moved = spaces.moved()
        for item in spaces.listed()["items"]:
            workspace, folder = item["id"], spaces.graph(item["id"])
            if folder is None or workspace in moved or not (folder / "neo4j-data").is_dir():
                continue
            try:
                with state.workspace_folder_graph(self.settings, folder) as source:
                    counts = state.copy_graph(source, space.driver, WorkspaceScope(workspace))
            except Exception as error:  # noqa: BLE001 -- its folder still holds it all
                state._log.exception("workspace %s was not moved into the one graph DB; its folder "
                               "is kept and the move is tried again at the next start",
                               workspace)
                self.unmoved[workspace] = str(error) or type(error).__name__
                continue
            spaces.mark_moved(workspace)
            self.unmoved.pop(workspace, None)
            state._log.info("moved workspace %s into the one graph DB: %s", workspace, counts)

    def _open_behind(self) -> None:
        try:
            with self.opening_space:
                self._opened_space()
            self.sweep_behind()  # what an earlier start left
        except Exception as error:  # noqa: BLE001 -- said to the next request
            self._open_failed = error

    def rag(self) -> GraphRag:
        """Questions over the graph space, opened on first use."""
        # Made under the space's lock too: two first questions at once make one engine.
        with self.opening_space:
            self.graph.graph_space()
            return self.graph.rag()

    def scope_of(self, paths: list[str], *,
                 space_of: Callable[[str | None], Any]) -> dict[str, Any]:
        """Files and folders on this machine as documents of the graph, found by their
        content. `workspace` is the one holding most of the files (the open one wins a tie),
        `document_ids` those it holds; `missing` the files no workspace holds, `elsewhere`
        those another workspace holds (never built), and `build_into` where a missing file
        may be built: that workspace, else the open one, never a mail workspace. `space_of`
        opens a workspace's graph (None: the only one). TooManyFiles past MAX_SCOPE_FILES,
        said as soon as the walk finds one more."""
        files: list[Path] = []
        missing: list[str] = []

        def counted(found: int) -> None:
            if len(files) + found > MAX_SCOPE_FILES:  # the rest of the walk is not waited for
                raise TooManyFiles

        for each in paths:
            try:
                files += documents_at(Path(each).expanduser().resolve(), on_found=counted)
            except (OSError, ValueError):  # not there, not a format read here
                missing.append(each)
            files = list(dict.fromkeys(files))  # a file and its folder both given
            if len(files) > MAX_SCOPE_FILES:
                raise TooManyFiles
        # Reading a file kept online only downloads it: given back once its content is known.
        clouded = [f for f in files if state.online_only(f)]
        try:
            hashes = {str(f): h for f in files if (h := state.content_hash(f))}
        finally:
            state._give_back_for_good(clouded)
        spaces = self.workspaces
        # no registry: the one open graph, under the id the settings give it
        kinds: dict[str, str] = {self.settings.workspace: "documents"}
        names: list[str] = [self.settings.workspace]
        if spaces is not None:
            active = spaces.active
            items = spaces.listed()["items"]
            kinds = {i["id"]: i.get("kind", "documents") for i in items}
            names = [active, *(i["id"] for i in items if i["id"] != active)]
        held: dict[str, dict[str, str]] = {}
        for name in names:
            try:
                held[name] = space_of(name if spaces else None).documents_of(
                    sorted(set(hashes.values())))
            except Exception:  # noqa: BLE001 -- a workspace beside that will not open
                if name == names[0]:
                    raise
                continue  # holds nothing here
        counts = {n: sum(h in held[n] for h in hashes.values()) for n in held}
        best = max(counts, key=lambda n: counts[n])  # the first of equals: the open one
        has = counts[best] > 0
        here = held[best] if has else {}
        elsewhere = [{"path": p, "workspace": next(n for n in held if n != best and h in held[n])}
                     for p, h in hashes.items()
                     if h not in here and any(h in held[n] for n in held if n != best)]
        gone = [p for p, h in hashes.items() if not any(h in x for x in held.values())]
        base: str | None = best if has else names[0]
        if kinds.get(base or "") == "mail":  # read from its mailbox, never built
            base = names[0] if kinds.get(names[0]) != "mail" else None
        return {"document_ids": list(dict.fromkeys(here[h] for h in hashes.values() if h in here)),
                "missing": list(dict.fromkeys(
                    [*missing, *(str(f) for f in files if str(f) not in hashes), *gone])),
                "elsewhere": elsewhere,
                "workspace": best if has else None, "build_into": base}

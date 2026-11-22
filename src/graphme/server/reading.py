"""Each document through the pipeline (`Session`): drawn, read, enriched, and shown as
the graph space keeps it."""
from __future__ import annotations

import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.visual_binding import pages_of, with_geometry
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.hierarchy_decisions import apply_hierarchy_decisions
from graphme.application.ingest import format_of
from graphme.application.progress import Progress
from graphme.domain.enums import FLOW_FORMATS, DocumentFormat, EdgeType
from graphme.domain.models import ExtractedDocument, HierarchyDecision, HierarchyDerivation
from graphme.runtime import page_readings
from graphme.server import state
from graphme.server.jobs import (
    CANCELLED,
    GONE,
    PROTECTED,
    STARTABLE,
    UNDRAWN,
    UNREAD,
    Cancelled,
    Session,
    Undrawn,
    _mark_failed,
)
from graphme.server.space_jobs import SpaceJobs


class Reading(SpaceJobs):
    """Each document's reading, in a worker under the shared model slots."""

    def pages_of_document(self, document_id: str) -> str:
        """The id of the file that draws the pages of a document in the graph space.

        A document read in this run already has them. One read before a restart opens from
        the pages kept for its content -- the file is not asked for, nor read to be hashed --
        and only pages never kept are drawn again from the file the space remembers:
        rendering only, no model call, its outline is the one in the space.
        """
        for doc, session in list(self.sessions.items()):
            if session.pages and session.extracted is not None and (
                session.extracted.document.id == document_id
            ):
                return doc
        space = self.space()
        path = space.source_path(document_id)
        if not path:
            raise LookupError(GONE)
        doc = self.open_path(path)
        session = self.session(doc)
        with session.lock:
            # Being read already: its own reading draws the pages, which the page waits for.
            if session.state not in ("idle", "error", "cancelled"):
                return doc
        # Built with where each node is drawn: shown by that, drawing only the page looked at.
        found = state.kept_from(space.document_graph(document_id), session.format)
        if found is not None:
            with session.lock:
                session.kept, session.pages = found
                session.state = "rendered"
            return doc
        with session.preparing:
            if session.pages:
                return doc
            with session.lock:
                session.state = "rendering"
            try:
                self._prepare(session)
            except FileNotFoundError as error:
                with session.lock:
                    session.state, session.error = "error", GONE
                raise LookupError(GONE) from error
            except LookupError as error:  # a cloud drive not signed in: said as it is
                with session.lock:
                    session.state, session.error = "error", str(error)
                raise
            except Exception as error:  # noqa: BLE001 -- said on the page
                traceback.print_exc(file=sys.stderr)
                with session.lock:
                    session.state, session.error = "error", UNDRAWN
                raise Undrawn(UNDRAWN) from error
            with session.lock:
                if session.state == "rendering":
                    session.state = "rendered"
            self._lighten(session)
        return doc

    def session(self, doc: str) -> Session:
        path = self.catalog.get(doc)
        if path is None:
            raise LookupError("this document cannot be found")
        # Two requests for a document nobody opened yet must end on one session; a second
        # one overwriting the first would leave its run going where no page can see it.
        with self.opening:
            open_ = self.sessions.get(doc)
            # A file edited since it was read is read anew; one being read now is left to it.
            # (One shown as the space holds it shows that version, whatever the file does now.)
            stale = open_ is not None and open_.kept is None and (
                open_.stamp != state._stamp(path)) and open_.state in (*STARTABLE, "done")
            if open_ is None or stale:
                source = Path(path)
                self.sessions[doc] = Session(
                    source=source,
                    workspace=self.root / doc,
                    format=format_of(source),
                    origin=self.origins[doc][0] if doc in self.origins else path,
                    stamp=state._stamp(path),
                )
            return self.sessions[doc]

    def start(self, doc: str, stop: threading.Event | None = None) -> Session:
        """Read a document, for the build `stop` stops, if any."""
        session = self.session(doc)
        with session.lock:
            # A document only drawn for the corpus view has not been read yet.
            if session.state not in STARTABLE:
                # Already being read -- an upload starts at once, before any build: the build
                # taking it in is the one whose cancel stops it.
                if stop is not None:
                    session.stop = stop
                return session
            session.state, session.error, session.stop = "rendering", None, stop
            session.kept = None  # read now: the reading is what is shown, not the space's
            session.waiting = self.readers.submit(self._run, session)
        return session

    def _run(self, session: Session) -> None:
        session.started_at, session.ended_at = time.monotonic(), None
        try:
            if session.format is DocumentFormat.EML:
                # A message has no pages and no mail body goes to a model: extracted, nothing more.
                self._steps(session, self._extract_message)
            else:
                self._steps(session, self._prepare, self._read, self._enrich, self._drawn)
        finally:
            session.ended_at = time.monotonic()
        self._lighten(session)

    @staticmethod
    def _lighten(session: Session) -> None:
        """The pages' pictures let go once read, in memory and on disk: a folder of thousands
        would otherwise hold every one. A page looked at later is drawn then (`preview`)."""
        session.pages = tuple(replace(page, png=b"") for page in session.pages)
        for page in session.pages:
            (session.workspace / f"page-{page.index:02d}.png").unlink(missing_ok=True)

    @staticmethod
    def _steps(session: Session, *steps: Callable[[Session], None]) -> None:
        """Run a document's steps in a worker; a failure is the document's state, not a crash.

        Only this says the document is finished. A step that announced `done` of its own
        accord said so while the steps after it were still to run, and the page -- which
        stops asking once a document is done -- kept whatever that moment held: an outline
        with no captions on it.
        """
        try:
            for step in steps:
                if session.stop is not None and session.stop.is_set():
                    raise Cancelled
                step(session)
            session.state = "done"
        except Cancelled:
            session.state, session.error = "cancelled", CANCELLED
        except Undrawn:  # the renderer failed, not the file
            traceback.print_exc(file=sys.stderr)
            session.state, session.error = "error", PROTECTED if state._sealed(session) else UNDRAWN
        except Exception:
            traceback.print_exc(file=sys.stderr)
            session.state, session.error = "error", PROTECTED if state._sealed(session) else UNREAD

    def _builder(self, session: Session) -> DocumentGraphBuilder:
        """The same graph builder the corpus build uses, over this document's
        pages as they were drawn once, under the slots every document shares."""
        return state.create_graph_builder(
            self.settings,
            renderer=state._Replay(session.pages),
            vision=state.create_vision(self.settings, self.client, limiter=self.vlm_slots),
            assets=session.store,
            workers=state.VLM_CALLS_AT_ONCE,
            progress=session.progress,
            readings=page_readings(self.settings),
        )

    def _read(self, session: Session) -> None:
        """Figures, then paragraphs with the outline a page behind -- the builder's order. Each
        page's outline is applied the moment it lands, so the view grows while the rest is read."""
        # Counted before any is asked: a page each, the outline's page breaks and headings too
        # in a flowing document -- each stage replaces its estimate with its own count.
        pages = len(session.pages)
        session.progress = Progress()
        session.progress.estimate(
            figures=pages, paragraphs=pages, captions=pages, references=pages,
            outline=2 * pages if session.format in FLOW_FORMATS else pages)
        builder = self._builder(session)
        session.state = "grouping"
        started = time.monotonic()
        session.extracted = builder.read_figures(session.read(), session.source)
        session.state = "reading"
        session.pending = {page.index for page in session.pages}
        by_index = {page.index: page for page in session.pages}
        # The grouping runs ahead of the outline; what lands is applied to the document joined
        # as far as it has come, with every page's outline that landed before it.
        grouped = [session.read()]
        landed_so_far: list[HierarchyDecision] = []

        def joined(so_far: ExtractedDocument) -> None:
            grouped[0] = so_far

        def landed(index: int, decisions: tuple[HierarchyDecision, ...] | None) -> None:
            # The pages not yet asked are not asked: a flowing document's come one after
            # another, and sheets' and slides' still queued are cancelled by the reader's pool;
            # only the ones already with the model finish.
            if session.stop is not None and session.stop.is_set():
                raise Cancelled
            unread = (index,) if decisions is None else ()
            derived = HierarchyDerivation(decisions=decisions or (), unread_pages=unread)
            with session.lock:
                session.extracted = apply_hierarchy_decisions(
                    grouped[0], state._standing(grouped[0], landed_so_far))
            self.apply(session, derived, by_index[index], state._since(started))
            landed_so_far.extend(derived.decisions)
            session.pending.discard(index)

        try:
            outlined = builder.group_and_read_outline(
                session.read(), session.source, on_page=landed, on_grouped=joined
            )
        except Cancelled:
            raise
        except Exception as error:  # noqa: BLE001 -- the pages say they failed; the rest goes on
            _mark_failed(session, tuple(by_index[i] for i in session.pending), error)
        else:
            with session.lock:
                session.extracted = outlined
        finally:
            session.pending.clear()

    def _enrich(self, session: Session) -> None:
        """What each visual shows, and what other documents the pages name."""

        def phase(name: str) -> None:
            session.state = name

        session.extracted = self._builder(session).enrich(
            session.read(), session.source, session.format, on_phase=phase
        )

    @staticmethod
    def _drawn(session: Session) -> None:
        """Where each node is drawn, written on the graph that goes into the space: a preview
        needs nothing else of this reading, and draws only the page looked at."""
        session.extracted = with_geometry(session.read(), session.pages, session.source)

    def _extract_message(self, session: Session) -> None:
        session.workspace.mkdir(parents=True, exist_ok=True)
        session.store = LocalAssetStore(self.settings.asset_dir)
        with self.sources.open_copy(session.origin or str(session.source)) as source:
            session.extracted = self._builder(session).build(source, session.format)

    def _prepare(self, session: Session) -> None:
        session.workspace.mkdir(parents=True, exist_ok=True)
        # The crops go where the graph space's answers read them (`GraphRag._pictures`), not
        # under this reading's workspace: they outlive it, and they are named by content.
        session.store = LocalAssetStore(self.settings.asset_dir)
        with self.sources.open_copy(session.origin or str(session.source)) as copy:
            # A Hangul document is read as the PDF it prints to, kept while it is read.
            with self.drawing:
                try:
                    source = self.renderer.printed(copy, session.workspace / "print")
                    session.pages = self.renderer.render(source, session.format)
                except Exception as error:
                    raise Undrawn(UNDRAWN) from error
            builder = self._builder(session)
            # Give tables their crop and let each own the pictures it encloses.
            bound = builder.bind(builder.extract(source, session.format), source)
        session.source = source  # what the reading's later steps read: the print, for HWP
        session.extracted = bound
        session.page_of = pages_of(bound, session.pages, session.format)
        # Shown while the document is read; gone once it is (`_lighten`).
        for page in session.pages:
            (session.workspace / f"page-{page.index:02d}.png").write_bytes(page.png)

    @staticmethod
    def apply(
        session: Session, derived: HierarchyDerivation, page: RenderedPage, seconds: float
    ) -> None:
        """Apply one page's answer and record the parents it states on that page."""
        with session.lock:
            if page.index in derived.unread_pages:
                session.errors[page.index] = (
                    "left without an outline: the VLM gave no usable answer")
            else:
                session.errors.pop(page.index, None)
            was = session.read()
            # Pages of a flowing document land one at a time, each read against the graph as
            # it was when the reading began; a node an earlier page dropped is simply gone.
            extracted = apply_hierarchy_decisions(was, state._standing(was, derived.decisions))
            session.extracted = extracted
            labels = {n.id: n.label for n in was.nodes}
            session.derived[page.index] = [
                {"node": labels[e.target_id][:70], "to": labels.get(e.source_id, "")[:50]}
                for e in extracted.edges
                if e.type is EdgeType.OUTLINE_PARENT
                and session.page_of.get(e.target_id) == page.index
            ]
            session.seconds[page.index] = seconds

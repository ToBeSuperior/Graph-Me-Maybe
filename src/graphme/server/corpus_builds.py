"""A build into the graph space (`CorpusBuild`): documents let in a few at a time, read,
put, linked and embedded; cancelled; and documents taken out again."""
from __future__ import annotations

import shutil
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path

from graphme.adapters.rendering.hwp_print import HWP_SUFFIXES, as_original
from graphme.application.corpus_build import (
    BuildReport,
    BuildStep,
    build_graph,
    relocated,
    with_origin,
)
from graphme.domain.models import ExtractedDocument
from graphme.server import state
from graphme.server.jobs import (
    BUILDING,
    CANCELLED,
    FINISHED,
    IN_LOG,
    STARTABLE,
    STEP_STATES,
    Busy,
    CorpusBuild,
    ExportJob,
)
from graphme.server.previews import Previews


class CorpusBuilds(Previews):
    """Builds into the graph space, embeddings, and documents deleted from it."""

    def build_corpus(
        self, docs: list[str], source: str, *, put: bool = True, embed: bool = True,
        find: Callable[[CorpusBuild], list[str]] | None = None, empty_done: bool = False,
    ) -> CorpusBuild:
        """Read every document, put them all in the graph space, then join what they name and
        -- when `embed` and an embedding key is set -- embed their passages. Not `put`, the
        build stops before the space: the files are read, and nothing else. `find` gives the
        documents instead, looked for once the build has started: a folder Box Drive lists by
        asking Box takes minutes to walk, and a request that walked it answered nothing."""
        with self.building:
            if self.build.state in BUILDING:
                raise Busy("a graph build is already running")
            # No total until the documents are looked through for the files they carry.
            self.build = CorpusBuild(id=uuid.uuid4().hex, kind="build" if put else "read",
                                     state="reading", source=source)
            self.jobs[self.build.id] = self.build
            self.build_stop = threading.Event()
            # One already being read -- an upload starts at once -- is this build's to stop
            # from now, not only once the build gets round to it.
            for doc in docs:
                session = self.sessions.get(doc)
                if session is not None:
                    with session.lock:
                        if session.state not in STARTABLE:
                            session.stop = self.build_stop
        threading.Thread(target=self._build_then_let_go,
                         args=(docs, put, embed, find, empty_done), daemon=True).start()
        return self.build

    def _build_then_let_go(self, docs: list[str], put: bool, embed: bool,
                           find: Callable[[CorpusBuild], list[str]] | None = None,
                           empty_done: bool = False) -> None:
        """The build, then the copies fetched from a cloud drive for it removed: the space
        records where each one is, and a reader is shown it from there."""
        build = self.build
        try:
            if find is not None:
                try:
                    docs = find(build)
                except (ValueError, OSError) as error:
                    build.state, build.error = "error", f"cannot be read: {error}"
                    return
                except Exception:  # noqa: BLE001 -- a job left reading for ever helps nobody
                    traceback.print_exc(file=sys.stderr)
                    build.state, build.error = "error", f"cannot be read {IN_LOG}"
                    return
                if not docs and empty_done:  # a mailbox with no mail yet: nothing to do
                    build.state = "done"
                    return
                if not docs:
                    build.state = "error"
                    build.error = "no readable document (xlsx, pptx, docx, pdf, hwp, hwpx, eml)"
                    return
            self._build_corpus(docs, put, embed)
        finally:
            build.ended_at = time.monotonic()
            for doc in docs:
                if doc in self.origins and (copy := self.catalog.get(doc)):
                    shutil.rmtree(Path(copy).parent, ignore_errors=True)
            if put and build.state not in BUILDING:
                try:
                    self._let_go_of_put(docs)
                except Exception:  # noqa: BLE001 -- held a while longer, not lost
                    traceback.print_exc(file=sys.stderr)
                self.sweep_behind()

    def start_embed(self) -> CorpusBuild:
        """Passages cut and embedded for every document not embedded yet, in the build's slot:
        never while a build is putting documents in."""
        with self.building:
            if self.build.state in BUILDING:
                raise Busy("a graph build is already running")
            job = self.build = CorpusBuild(id=uuid.uuid4().hex, kind="embed", state="embedding")
            self.jobs[job.id] = job
        threading.Thread(target=self._embed_alone, args=(job,), daemon=True).start()
        return job

    def cancel(self, job: CorpusBuild | ExportJob) -> CorpusBuild:
        """Stop a build or a reading. An embedding or an export is not stopped half way."""
        if not isinstance(job, CorpusBuild) or job.kind == "embed":
            raise Busy("only a build or a reading can be cancelled")
        return self.cancel_build() if job is self.build else job

    def cancel_build(self) -> CorpusBuild:
        """Stop the build under way, if one is: a document still waiting its turn is passed over
        now, one being read stops before its next step."""
        with self.building:
            if self.build.state in BUILDING:
                self.build_stop.set()
                for session in list(self.sessions.values()):
                    with session.lock:
                        if session.stop is self.build_stop and session.waiting is not None \
                                and session.waiting.cancel():
                            session.state, session.error = "cancelled", CANCELLED
        return self.build

    def _build_corpus(self, docs: list[str], put: bool, embed: bool) -> None:
        build, stop = self.build, self.build_stop
        # Documents are let in a few at a time: as many as are read at once, and as many again
        # waiting their turn. Letting one in reads its content (is it in the graph already? what
        # files does it carry?), and a cloud drive downloads a file read and keeps it: a whole
        # folder let in at once filled the disk faster than it was read.
        waiting = deque(dict.fromkeys(docs))
        given = set(waiting)
        admitted: list[str] = []
        carried: set[str] = set()
        clouded: set[str] = set()
        """Online only before this build read them: given back to the cloud once read."""
        given_back: set[str] = set()
        fed = threading.Event()
        window = 2 * self.reading_at_once
        build.total = len(waiting)

        giving = threading.Lock()

        def give_back(chosen: Iterable[str]) -> None:
            with giving:  # the feeder and the putting both give back: each file once
                chosen = [doc for doc in chosen if doc in clouded and doc not in given_back]
                given_back.update(chosen)
            paths = [Path(self.catalog[doc]) for doc in chosen if doc in self.catalog]
            if paths and (freed := state.evict(paths)):
                state._log.info("gave %s files read back to their cloud drive", freed)

        def give_back_again() -> None:
            """What was given back and downloaded again since (an indexer read it) given back."""
            with giving:
                again = [Path(self.catalog[doc]) for doc in given_back
                         if doc in self.catalog and not state.online_only(Path(self.catalog[doc]))]
            if again:
                state.evict(again)

        def feed() -> None:
            try:
                while waiting and not stop.is_set():
                    # A document read is given back before another is let in in its place.
                    give_back(d for d in admitted if self.sessions[d].state in FINISHED)
                    give_back_again()
                    if sum(self.sessions[d].state not in FINISHED for d in admitted) >= window:
                        time.sleep(0.2)
                        continue
                    doc = waiting.popleft()
                    path = self.catalog.get(doc)
                    if path and state.online_only(Path(path)):
                        clouded.add(doc)
                    # A file whose content the space already holds is not read again: reading
                    # costs a model call a page. A reading alone never asks the space.
                    if put and self.already_in([doc]):
                        build.known += 1
                        build.total -= 1
                        if doc in carried:
                            build.carried -= 1
                        if path:
                            relocated(self.space(), {path: self.content_hash(doc)})
                        give_back([doc])
                        continue
                    # A file a document carries -- a Word file on a slide -- is read as a
                    # document of its own, kept the way an upload is; one level, not what it
                    # carries in turn.
                    if doc in given and path:
                        found = [self.add(each.name, each.content)
                                 for each in state.embedded_in(Path(path)) if each.content]
                        fresh = [c for c in dict.fromkeys(found) if c not in given
                                 and c not in carried and c not in admitted]
                        carried.update(fresh)
                        waiting.extendleft(reversed(fresh))
                        build.carried += len(fresh)
                        build.total += len(fresh)
                    if put:
                        self.putting.add(doc)
                    self.start(doc, stop)  # read side by side, under the shared slots
                    # Counted as let in once its reading exists: the build looks it up.
                    admitted.append(doc)
            except Exception:  # noqa: BLE001 -- the documents let in are still read and put
                traceback.print_exc(file=sys.stderr)
            finally:
                fed.set()

        def finished() -> int:
            # The documents are read side by side and finish in any order, and are put in the
            # order they finish, so how far it is is how many are read.
            return sum(self.sessions[doc].state in FINISHED for doc in list(admitted))

        def share() -> int:
            """How far the reading is: each document read is whole, each being read as far as
            its questions answered, each waiting for a reader not begun."""
            def one(doc: str) -> float:
                session = self.sessions[doc]
                if session.state in FINISHED:
                    return 1.0
                done, planned = session.progress.counts()
                return done / planned if planned else 0.0
            let_in = list(admitted)
            whole = len(let_in) + len(waiting)
            return int(100 * sum(one(doc) for doc in let_in) / whole) if whole else 100

        def counted() -> int:
            build.percent = max(build.percent or 0, share())  # an estimate replaced: no step back
            return finished()

        def in_turn(_: Sequence[Path]) -> Iterator[Path]:
            """Each document let in, once it is read -- its file given back to the cloud then;
            when a cancel or a reading that never ends stops the wait, the rest, which
            `process` then says were not read."""
            yielded: set[str] = set()
            deadline = time.monotonic() + state.READ_TIMEOUT_SECONDS
            while True:
                done_feeding = fed.is_set()
                left = [doc for doc in admitted if doc not in yielded]
                if not left and done_feeding:
                    return
                read = [doc for doc in left if self.sessions[doc].state in FINISHED]
                if not read and left and (stop.is_set() or time.monotonic() > deadline):
                    read = left
                if not read:
                    build.done = counted()
                    time.sleep(0.2)
                    continue
                yielded.update(read)
                deadline = time.monotonic() + state.READ_TIMEOUT_SECONDS
                give_back(doc for doc in read if self.sessions[doc].state in FINISHED)
                yield from (Path(doc) for doc in read)

        def process(handle: Path) -> ExtractedDocument:
            session = self.sessions[handle.name]
            if session.state not in FINISHED:
                if stop.is_set():
                    raise RuntimeError(f"{session.source.name}: {CANCELLED}")
                raise RuntimeError(f"{session.source.name}: not read in time")
            if session.state != "done" or session.extracted is None:
                raise RuntimeError(f"{Path(session.source).name}: {session.error}")
            self.putting.discard(handle.name)  # taken by the space now, not waiting for it
            read = session.extracted
            original = Path(self.catalog[handle.name])
            if original.suffix.lower() in HWP_SUFFIXES and original.is_file():
                read = as_original(read, original)  # the file given, not the print read
            if handle.name in self.origins:
                return with_origin(read, *self.origins[handle.name])
            return read

        def step(name: BuildStep, done: int, total: int) -> None:
            build.state = STEP_STATES[name]
            if name == BuildStep.LINK:  # the reading's total is the build's, as it grows
                build.total, build.done = total, done
            else:
                build.done = counted()

        def read_only() -> BuildReport:
            failed = []
            for path in in_turn([]):
                try:
                    process(path)
                except RuntimeError as error:
                    failed.append((path, str(error)))
            build.done = counted()
            return BuildReport(processed=len(admitted) - len(failed), failed=tuple(failed),
                               links=0)

        self.putting = set()
        threading.Thread(target=feed, daemon=True).start()
        try:
            # The documents arrive as they are let in and read (`in_turn`), not as a list.
            report = build_graph(
                [], process=process, space=self.space(), on_step=step, in_turn=in_turn,
            ) if put else read_only()
        except Exception:  # noqa: BLE001 -- the build's state says what happened
            traceback.print_exc(file=sys.stderr)
            build.state, build.error = "error", f"the graph could not be built {IN_LOG}"
            return
        finally:
            self.putting = set()
            fed.wait()
            # Whatever was read and not given back yet -- one passed over, one that failed.
            give_back(doc for doc in list(clouded) if doc not in self.sessions
                      or self.sessions[doc].state in FINISHED or doc not in admitted)
            build.skipped += len(waiting)  # a cancel left them waiting: never let in
            if given_back:
                threading.Thread(target=state._settle, args=(give_back_again,), daemon=True).start()
        # What failed, said by file name -- the server's folders are this machine's, not the
        # reader's; what a cancel passed over is not a failure.
        # Kept beside what was said before the reading: a folder passed over while looking.
        build.failed += [reason.replace(str(path), path.name).replace(f"{self.root}/", "")
                         for path, reason in report.failed if not reason.endswith(CANCELLED)]
        build.skipped = sum(1 for _, reason in report.failed if reason.endswith(CANCELLED))
        build.links, build.documents = report.links, report.processed
        # What the question panel searches: the passages of every document just put in.
        if put and embed and self.settings.embeds and not self._embed(build):
            return
        build.state = "cancelled" if stop.is_set() else "done"

    def _embed(self, build: CorpusBuild) -> bool:
        """The passages not embedded yet embedded, said on `build` as it goes; False, with the
        error said there, when they could not be."""

        def embedded(done: int, total: int) -> None:
            build.done, build.total = done, total

        build.state, build.done, build.total = "embedding", 0, 0
        try:
            build.embedded = self.rag().embed(on_progress=embedded)
        except Exception:  # noqa: BLE001 -- the build's state says what happened
            traceback.print_exc(file=sys.stderr)
            build.state = "error"
            build.error = f"the passages could not be embedded for search {IN_LOG}"
            return False
        return True

    def _embed_alone(self, build: CorpusBuild) -> None:
        try:
            if self._embed(build):
                build.state = "done"
        finally:
            build.ended_at = time.monotonic()

    def delete_documents(self, ids: list[str]) -> None:
        """Documents gone from the graph space, and the server's own reading of each, the pages
        kept for readers and the crops nothing names any more with them. KeyError: one is not
        in the space (nothing is deleted)."""
        space = self.space()
        paths = {i: space.source_path(i) for i in ids}
        self.graph.delete(ids)
        for path in filter(None, paths.values()):
            # Its readings, found by the file they read -- one built this run is held as the
            # graph keeps it, no longer as read: a document deleted is not left behind.
            self.drop([doc for doc, p in list(self.catalog.items())
                       if path in (p, self.origins.get(doc, ("",))[0])])
            self.forget_previews(path)  # its pages kept for readers
        self.sweep_behind()  # their crops

    def already_in(self, docs: list[str]) -> list[str]:
        """The documents among `docs` whose content the graph space already holds; none when
        it cannot be asked."""
        hashes = {doc: self.content_hash(doc) for doc in docs}
        try:
            held = self.space().contents([h for h in hashes.values() if h])
        except Exception:  # noqa: BLE001 -- read them, rather than fail on the asking
            return []
        return [doc for doc, h in hashes.items() if h in held]

    def content_hash(self, doc: str) -> str | None:
        """The SHA-256 of a document's file -- what the graph space knows it by -- read again
        only when the file's size or time says it changed."""
        stamp = state._stamp(self.catalog[doc])
        kept = self.hashes.get(doc)
        if kept is None or kept[0] != stamp:
            digest = state.content_hash(Path(self.catalog[doc]))
            if digest is None:
                return None
            kept = self.hashes[doc] = (stamp, digest)
        return kept[1]

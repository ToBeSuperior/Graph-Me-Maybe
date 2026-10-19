"""The server's state: each file through the pipeline (`Session`), a build into the graph
space (`CorpusBuild`), an export to Neo4j (`ExportJob`), and `Demo`, which holds them and
runs the work in threads. The HTTP routes over it are `app.py`; nothing here knows HTTP --
a refusal is `Busy`, `LookupError` or `ValueError`, and the route says what it means."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
import weakref
import zipfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx

from graphme.adapters.export.neo4j_admin import LocalNeo4jAdmin
from graphme.adapters.export.neo4j_import import write_import_bundle
from graphme.adapters.rendering import page_cache
from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.adapters.storage.graph_rag import GraphRag
from graphme.adapters.storage.local_neo4j import GraphStarting
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.api import GraphMe
from graphme.application.corpus_build import BuildReport, BuildStep, build_graph, content_hash
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.embedded_documents import embedded_in, safe_name, shown_name
from graphme.application.hierarchy_decisions import apply_hierarchy_decisions
from graphme.application.ingest import SUFFIX_FORMATS, format_of
from graphme.application.page_units import flow_pages
from graphme.config import Settings
from graphme.domain.enums import DocumentFormat, EdgeType
from graphme.domain.ids import sha256_bytes
from graphme.domain.models import ExtractedDocument, HierarchyDecision, HierarchyDerivation
from graphme.runtime import create_graph_builder, create_neo4j_admin, create_vision

VLM_CALLS_AT_ONCE = 24
"""Model calls in flight across every document, each reading and each settling call counted
on its own. Lower it when the endpoint's rate limit turns calls away; a call turned away is
retried, so too high costs time rather than pages."""
READERS = 8
"""Documents read side by side; their model calls share VLM_CALLS_AT_ONCE besides."""

Placed = dict[str, tuple[int, tuple[int, int, int, int]]]
"""Where each node is drawn: its page and its pixel box there (`placed_units`)."""


class Busy(Exception):
    """Refused because the work it would disturb is under way: a build, an export, a
    reading. Said as 409."""


class Undrawn(Exception):
    """A document's pages could not be drawn again. Said as 503: the file is there, the
    renderer is what failed."""


def page_name(page: RenderedPage, fmt: DocumentFormat) -> str | None:
    """A sheet's own name. Any other page the page names by its number, in its own language."""
    return page.label if fmt is DocumentFormat.XLSX and page.label else None


def pages_of(
    extracted: ExtractedDocument, pages: Sequence[RenderedPage], fmt: DocumentFormat
) -> dict[str, int]:
    """The page each node is drawn on -- a worksheet names it by sheet, a slide by number."""
    if fmt is DocumentFormat.DOCX:
        return dict(flow_pages(extracted, pages, fmt))
    if fmt is DocumentFormat.XLSX:
        by_sheet = {page.label: page.index for page in pages}
        return {
            n.id: by_sheet[n.locator.sheet] for n in extracted.nodes if n.locator.sheet in by_sheet
        }
    numbers = {page.index for page in pages}
    found = {
        n.id: n.locator.page if fmt is DocumentFormat.PDF else n.locator.slide
        for n in extracted.nodes
    }
    return {node: number for node, number in found.items() if number is not None
            and number in numbers}


@dataclass
class _Replay:
    """Hands back pages already rendered, so nothing is drawn twice."""

    pages: tuple[RenderedPage, ...]

    def render(self, source: Path, document_format: DocumentFormat) -> tuple[RenderedPage, ...]:
        return self.pages


def _since(started: float) -> float:
    return round(time.monotonic() - started, 1)


# What failed is said on the page plainly; what it was -- with this machine's paths in it --
# is in the server's log, printed where it happened.
IN_LOG = "(자세한 내용은 서버 로그)"
SPACE_WAIT = 2.0
UNREAD = f"읽지 못했습니다 -- 손상됐거나 읽을 수 없는 내용의 파일일 수 있습니다 {IN_LOG}"
PAGE_UNREAD = f"이 페이지를 읽지 못했습니다 {IN_LOG}"
UNDRAWN = f"페이지를 그리지 못했습니다 {IN_LOG}"


def _mark_failed(session: Session, pages: Iterable[RenderedPage], error: Exception) -> None:
    traceback.print_exc(file=sys.stderr)
    for page in pages:
        session.errors[page.index] = PAGE_UNREAD


@dataclass
class Session:
    """One document through the pipeline, and what the page needs to show it."""

    source: Path
    workspace: Path
    format: DocumentFormat
    state: str = "idle"
    """idle | rendering | grouping | reading | captioning | referencing | done | error"""
    error: str | None = None
    extracted: ExtractedDocument | None = None
    bound: ExtractedDocument | None = None
    """The graph as binding left it, before any outline: what every reading starts from."""
    store: LocalAssetStore | None = None
    pages: tuple[RenderedPage, ...] = ()
    page_of: dict[str, int] = field(default_factory=dict)
    """The page each node is drawn on (the first, for one crossing a break). Measured once
    after binding; a DOCX page has to be laid out again to say it."""
    derived: dict[int, list[dict[str, str]]] = field(default_factory=dict)
    seconds: dict[int, float] = field(default_factory=dict)
    pending: set[int] = field(default_factory=set)
    errors: dict[int, str] = field(default_factory=dict)
    waiting: Future[None] | None = None
    """Its reading, queued for a reader: cancelled outright while it has not started."""
    stop: threading.Event | None = None
    """The build reading it, stopped: checked between its steps, so a cancel spends no more
    model calls on it than the step under way. None when no build is reading it."""
    placed: tuple[ExtractedDocument, Placed] | None = None
    """Where each node is drawn (`placed_units`), with the graph it was measured on: measured
    again only once the graph has changed."""
    # Every replacement of the graph goes through it: pages are read at once and each
    # answer is applied to whatever the others left.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self) -> ExtractedDocument:
        """The graph as it stands; a step that needs one runs only after `_prepare` made it."""
        if self.extracted is None:
            raise RuntimeError(f"{self.source.name} has not been extracted")
        return self.extracted


@dataclass
class ExportJob:
    """Where the space's export to Neo4j stands: a `.dump` being made, then -- for an upload
    -- that dump being pushed to Aura. Never holds the Aura password."""

    id: str = ""
    """Its job id; none for the empty slot a start begins with."""
    kind: str = ""  # dump | aura-upload
    state: str = "idle"  # dumping -> uploading -> done | error
    message: str = ""
    documents: int | None = None
    """How many documents it carries, once known."""
    finished_at: float | None = None
    """When it was done, in seconds since 1970."""


EXPORTING = ("dumping", "uploading")


@dataclass
class CorpusBuild:
    """Where a folder's build stands: each document read, then all put in the graph space,
    then the documents they name joined -- one step after another, never interleaved."""

    id: str = ""
    """Its job id; none for the empty slot a start begins with."""
    kind: str = "build"
    """build (read and put in the space) | read (read only) | embed (passages embedded only)"""
    state: str = "idle"  # reading -> linking -> embedding -> done | cancelled | error
    done: int = 0
    total: int = 0
    """How far the step in progress is."""
    source: str = ""
    failed: list[str] = field(default_factory=list)
    documents: int = 0
    """Documents put in the graph DB -- for a reading, documents read."""
    known: int = 0
    """Documents passed over: their content was in the graph DB already."""
    carried: int = 0
    """Files found inside the documents given, read as documents of their own and counted in
    `total`: said apart, so a total that takes them in does not look like a jump."""
    links: int = 0
    skipped: int = 0
    """Documents a cancel passed over."""
    embedded: int = 0
    """Passages embedded for questions."""
    error: str | None = None


# A document is put in the graph DB as soon as it is read: one step to the reader.
STEP_STATES = {BuildStep.READ: "reading", BuildStep.PUT: "reading", BuildStep.LINK: "linking"}
BUILDING = ("reading", "linking", "embedding")
"""A build's states before it is done; embedding follows the three steps when a key is set."""
STARTABLE = ("idle", "error", "cancelled", "rendered")
"""States a document's reading can begin from: never started, failed, cancelled, or only
drawn."""
FINISHED = ("done", "error", "cancelled")
"""States a document's reading ends in."""
CANCELLED = "빌드를 취소해 읽지 않음"
"""Why a document a stopped build passed over was not read -- the end of its reason."""


class Cancelled(Exception):
    """A document's reading stopped between steps because its build was cancelled."""


READ_TIMEOUT_SECONDS = 4 * 60 * 60
"""How long a build waits for one document's reading before counting it as failed."""
ROOT_PREFIX = "hierarchy-demo-"
"""What a working folder made for a moment (a test) is called in the system's temporary
folder."""


class Demo:
    def __init__(
        self, settings: Settings, renderer: PageRenderer, root: Path | None = None
    ) -> None:
        # The same settings build-graph reads: model, endpoint, readings. A demo that picked its
        # own defaults showed an outline the pipeline would not have produced.
        self.settings = settings
        self.renderer = renderer
        if root is None:
            # A folder of its own, gone with it: a demo made for a moment (a test) leaves none.
            self.root = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX))
            weakref.finalize(self, shutil.rmtree, self.root, True)
        else:
            # The same folder each start: an uploaded file the graph points to is still there.
            self.root = root
            self.root.mkdir(parents=True, exist_ok=True)
        self.graph = GraphMe(settings)
        """The graph space and its questions: one Neo4j connection for every request. Opened
        under `opening_space`, the one thing about it not safe from two threads at once, and so is
        its RAG engine."""
        # ponytail: every job since this start, never let go -- a few a start; keep the last N
        # if a server that runs for months appears.
        self.jobs: dict[str, CorpusBuild | ExportJob] = {}
        """Every job since this start, oldest first, by its id."""
        self.sessions: dict[str, Session] = {}
        # One browser drawing at a time; the model calls are what run side by side.
        self.drawing = threading.Lock()
        self.vlm_slots = threading.Semaphore(VLM_CALLS_AT_ONCE)
        self.opening = threading.Lock()
        # One pool of connections to the endpoint for every reading, not a new one per page.
        self.client = httpx.Client(timeout=600.0)
        # Every image and every poll looks a document up here: every document a folder brought.
        self.catalog: dict[str, str] = {}
        self.readers = ThreadPoolExecutor(max_workers=READERS, thread_name_prefix="read")
        """Documents read at once: a folder of thousands queues here, not in a thread each."""
        self.hashes: dict[str, str] = {}
        """Each document's content hash, worked out once (`content_hash`)."""
        self.build = CorpusBuild()
        self.export = ExportJob()
        self.exporting = threading.Lock()
        self.dump_path = self.root / "neo4j.dump"
        self.dump_path.unlink(missing_ok=True)  # made by an earlier start, not this one
        self.building = threading.Lock()
        self.build_stop = threading.Event()
        """Set to stop the build under way -- a new one each build, so a cancel never outlives
        it: documents not yet read are passed over, what is in the graph stays searchable."""
        # A document's pages are drawn by one request; another asking meanwhile waits for
        # those pages instead of drawing its own.
        self.preparing = threading.Lock()
        self.opening_space = threading.Lock()
        self._opener: threading.Thread | None = None
        self._starting_opener = threading.Lock()
        self._open_failed: Exception | None = None

    def reconfigure(self, settings: Settings) -> None:
        """New keys or model, taken up from the next reading and the next question. The graph
        space is kept: what it is reached by is not among what changes."""
        self.settings = settings
        self.graph.settings = settings
        with self.opening_space:
            self.graph._rag = None  # made again, with the new keys, on the next question

    def space(self, *, wait: float = -1) -> Neo4jGraphSpace:
        """The graph space every built document is put in, opened on first use. A build waits
        for it however long; a page's request (`wait`) only so long."""
        # The first use may start the local Neo4j; the page asks for the list and the map at once.
        if wait < 0:
            with self.opening_space:
                return self.graph.graph_space()
        if self.graph._space is not None:
            return self.graph._space
        # Pulling and starting Neo4j takes minutes, so a page's request never does it: it is done
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
            raise GraphStarting("그래프 DB를 준비하는 중입니다 -- 준비되면 저절로 이어집니다.")
        if (failed := self._open_failed) is not None:
            self._open_failed = None
            raise failed
        return self.graph.graph_space()

    def _open_behind(self) -> None:
        try:
            with self.opening_space:
                self.graph.graph_space()
        except Exception as error:  # noqa: BLE001 -- said to the next request
            self._open_failed = error

    def admin(self) -> LocalNeo4jAdmin:
        """`neo4j-admin`, in a throwaway container of the configured Neo4j image."""
        return create_neo4j_admin(self.settings)

    def bundle_zip(self, docs: list[str] | None) -> Path:
        """The space -- or the documents `docs` names -- as a zip of what
        `neo4j-admin database import` reads: a file of its own per request, which the caller
        removes once it is sent."""
        with tempfile.TemporaryDirectory() as folder:
            write_import_bundle(self.space().corpus_rows(docs), Path(folder))
            place = Path(tempfile.mkdtemp(prefix="neo4j-import-"))  # a dropped download: temp
            return Path(shutil.make_archive(str(place / "neo4j-import"), "zip", folder))

    def files_zip(self, picked: Sequence[tuple[str, str]]) -> Path:
        """The files the documents in `picked` were read from, each in the folder named beside
        it, as one zip of its own per request, which the caller removes once it is sent. A
        document the space does not hold, or whose file is gone, is named in
        `missing-files.txt` rather than passed over."""
        space = self.space()
        place = Path(tempfile.mkdtemp(prefix="graphme-files-"))  # a dropped download: temp
        archive = place / "files.zip"
        taken: set[str] = set()
        missing: list[str] = []
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
            for document_id, folder in picked:
                path = space.source_path(document_id)
                if not path or not Path(path).is_file():
                    missing.append(shown_name(Path(path).name) if path else document_id)
                    continue
                # A folder name is one folder: no separator, no way out of the zip.
                where = "".join(c for c in folder if c not in '/\\:*?"<>|').strip(". ") or "files"
                stem, suffix = os.path.splitext(shown_name(Path(path).name))
                name, n = f"{where}/{stem}{suffix}", 1
                while name in taken:  # two files of one name in one folder: both kept
                    n += 1
                    name = f"{where}/{stem} ({n}){suffix}"
                taken.add(name)
                out.write(path, name)
            if missing:
                out.writestr("missing-files.txt", "\n".join(missing) + "\n")
        return archive

    def start_export(
        self, kind: str, docs: list[str] | None, aura: dict[str, str] | None = None
    ) -> ExportJob:
        """A `.dump` of the space, and for an upload that dump pushed to Aura, in the
        background. `aura` -- address, user, password -- lives only in the thread."""
        with self.exporting:
            if self.export.state in EXPORTING:
                raise Busy("이미 내보내는 중입니다")
            job = self.export = ExportJob(
                id=uuid.uuid4().hex, kind=kind, state="dumping", message="dump를 만드는 중")
            self.jobs[job.id] = job
        threading.Thread(target=self._export, args=(job, docs, aura), daemon=True).start()
        return job

    def _export(
        self, job: ExportJob, docs: list[str] | None, aura: dict[str, str] | None
    ) -> None:
        try:
            self.space()  # opened under `opening_space`, like every other first use
            job.documents = self.graph.export(self.dump_path, dump=True, documents=docs)
            if aura is not None:
                job.state, job.message = "uploading", "Aura에 올리는 중"
                said = self.admin().upload(self.dump_path, **aura)
                job.message = said.strip().splitlines()[-1] if said.strip() else ""
            else:
                job.message = f"{self.dump_path.stat().st_size // 1024} KB"
            job.finished_at = time.time()
            job.state = "done"
        except Exception as error:  # noqa: BLE001 -- the job's state says what happened
            # The log has all of it; the page, its first line -- never the password.
            traceback.print_exc(file=sys.stderr)
            said = (str(error).strip().splitlines() or [type(error).__name__])[0][:300]
            if aura and aura.get("password"):
                said = said.replace(aura["password"], "***")
            job.state, job.message = "error", f"{said} {IN_LOG}"

    def rag(self) -> GraphRag:
        """Questions over the graph space, opened on first use."""
        # Made under the space's lock too: two first questions at once make one engine.
        with self.opening_space:
            self.graph.graph_space()
            return self.graph._graph_rag()

    def pages_of_document(self, document_id: str) -> str:
        """The id of the file that draws the pages of a document in the graph space.

        A document read in this run already has them. One read before a restart is found
        by the file the space remembers and drawn again -- rendering only, no model call:
        its outline is the one in the space.
        """
        for doc, session in list(self.sessions.items()):
            if session.pages and session.extracted is not None and (
                session.extracted.document.id == document_id
            ):
                return doc
        path = self.space().source_path(document_id)
        if not path or not Path(path).is_file():
            raise LookupError("이 문서의 원본 파일을 찾을 수 없습니다")
        doc = self.open_path(path)
        session = self.session(doc)
        with session.lock:
            # Being read already: its own reading draws the pages, which the page waits for.
            if session.state not in ("idle", "error", "cancelled"):
                return doc
        with self.preparing:
            if session.pages:
                return doc
            with session.lock:
                session.state = "rendering"
            try:
                self._prepare(session)
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

    def open_path(self, path: str) -> str:
        """A document on this machine, opened by its path, and its id."""
        doc = self.doc_id(path)
        with self.opening:
            self.catalog.setdefault(doc, path)
        return doc

    def build_corpus(
        self, docs: list[str], source: str, *, put: bool = True, embed: bool = True
    ) -> CorpusBuild:
        """Read every document, put them all in the graph space, then join what they name and
        -- when `embed` and an embedding key is set -- embed their passages. Not `put`, the
        build stops before the space: the files are read, and nothing else."""
        with self.building:
            if self.build.state in BUILDING:
                raise Busy("이미 그래프를 만드는 중입니다")
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
        threading.Thread(target=self._build_corpus, args=(docs, put, embed), daemon=True).start()
        return self.build

    def start_embed(self) -> CorpusBuild:
        """Passages cut and embedded for every document not embedded yet, in the build's slot:
        never while a build is putting documents in."""
        with self.building:
            if self.build.state in BUILDING:
                raise Busy("이미 그래프를 만드는 중입니다")
            job = self.build = CorpusBuild(id=uuid.uuid4().hex, kind="embed", state="embedding")
            self.jobs[job.id] = job
        threading.Thread(target=self._embed_alone, args=(job,), daemon=True).start()
        return job

    def cancel(self, job: CorpusBuild | ExportJob) -> CorpusBuild:
        """Stop a build or a reading. An embedding or an export is not stopped half way."""
        if not isinstance(job, CorpusBuild) or job.kind == "embed":
            raise Busy("적재와 읽기만 취소할 수 있습니다")
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
        # A file a document carries -- a Word file on a slide -- is read as a document of its
        # own, kept the way an upload is; one level, not what it carries in turn. Found here,
        # off the request: a large PDF takes a while to look through.
        given = list(dict.fromkeys(docs))
        docs = list(dict.fromkeys([*given, *(
            self.add(each.name, each.content)
            for doc in given if doc in self.catalog
            for each in embedded_in(Path(self.catalog[doc])) if each.content
        )]))
        # A file whose content the space already holds is not read again: reading costs a
        # model call a page. A reading alone never asks the space.
        known = set(self.already_in(docs)) if put else set()
        build.carried = sum(doc not in known for doc in docs[len(given):])
        docs = [doc for doc in docs if doc not in known]
        build.known, build.total = len(known), len(docs)
        for doc in docs:
            self.start(doc, stop)  # every document reads side by side, under the shared slots

        def finished() -> int:
            # The documents are read side by side and finish in any order; the build puts them
            # in the order given, so how far it is is how many are read, not where it is.
            return sum(self.sessions[doc].state in FINISHED for doc in docs)

        def process(handle: Path) -> ExtractedDocument:
            session = self.sessions[handle.name]
            deadline = time.monotonic() + READ_TIMEOUT_SECONDS
            while session.state not in FINISHED:
                build.done = finished()
                if stop.is_set():
                    raise RuntimeError(f"{session.source.name}: {CANCELLED}")
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{session.source.name}: not read in time")
                time.sleep(1)
            if session.state != "done" or session.extracted is None:
                raise RuntimeError(f"{Path(session.source).name}: {session.error}")
            return session.extracted

        def step(name: BuildStep, done: int, total: int) -> None:
            build.state, build.total = STEP_STATES[name], total
            build.done = done if name == BuildStep.LINK else finished()

        def read_only() -> BuildReport:
            failed = []
            for doc in docs:
                try:
                    process(Path(doc))
                except RuntimeError as error:
                    failed.append((Path(doc), str(error)))
            build.done = finished()
            return BuildReport(processed=len(docs) - len(failed), failed=tuple(failed), links=0)

        try:
            report = build_graph(
                [Path(doc) for doc in docs], process=process, space=self.space(), on_step=step
            ) if put else read_only()
        except Exception:  # noqa: BLE001 -- the build's state says what happened
            traceback.print_exc(file=sys.stderr)
            build.state, build.error = "error", f"그래프를 만들지 못했습니다 {IN_LOG}"
            return
        # What failed, said by file name -- the server's folders are this machine's, not the
        # reader's; what a cancel passed over is not a failure.
        build.failed = [reason.replace(str(path), path.name).replace(f"{self.root}/", "")
                        for path, reason in report.failed if not reason.endswith(CANCELLED)]
        build.skipped = sum(1 for _, reason in report.failed if reason.endswith(CANCELLED))
        build.links, build.documents = report.links, report.processed
        # What the question panel searches: the passages of every document just put in.
        if put and embed and self.settings.embedding_key is not None and not self._embed(build):
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
            build.state, build.error = "error", f"검색 준비(임베딩)를 하지 못했습니다 {IN_LOG}"
            return False
        return True

    def _embed_alone(self, build: CorpusBuild) -> None:
        if self._embed(build):
            build.state = "done"

    def drop(self, docs: Iterable[str]) -> None:
        """The server's own copies of these files let go: their readings, files and hashes."""
        with self.opening:
            for doc in list(docs):
                self.sessions.pop(doc, None)
                self.catalog.pop(doc, None)
                self.hashes.pop(doc, None)

    def forget(self, document_id: str) -> None:
        """The server's own reading of a document deleted from the graph let go too: a document
        deleted is gone, not left behind as one read but outside the graph."""
        self.drop([doc for doc, session in list(self.sessions.items())
                   if session.extracted is not None
                   and session.extracted.document.id == document_id])

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
        """The SHA-256 of a document's file -- what the graph space knows it by -- read once."""
        if doc not in self.hashes:
            digest = content_hash(Path(self.catalog[doc]))
            if digest is None:
                return None
            self.hashes[doc] = digest
        return self.hashes[doc]

    @staticmethod
    def doc_id(path: str) -> str:
        return hashlib.sha1(path.encode()).hexdigest()[:10]

    def add(self, name: str, content: bytes, modified: float | None = None) -> str:
        """Keep an uploaded document beside the server's other work and return its id.

        Named by what is in it, so the same file uploaded twice is one document that runs
        once. It stays in the server's folder on this machine and nowhere else, and carries
        `modified` -- when the reader's own file was last changed -- as its own modification
        time, as the file did.
        """
        suffix = Path(name).suffix.lower()
        if suffix not in SUFFIX_FORMATS:
            raise ValueError(f"지원하지 않는 형식입니다 ({', '.join(SUFFIX_FORMATS)})")
        if not content:
            raise ValueError("빈 파일입니다")
        digest = sha256_bytes(content)
        doc = digest[:10]
        self.hashes[doc] = digest
        with self.opening:
            if doc not in self.catalog:
                # Kept under a name any file system takes: a browser sends whatever it was given.
                target = self.root / "uploads" / doc / safe_name(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + ".part")
                partial.write_bytes(content)
                os.replace(partial, target)  # a write cut short leaves no half file under its name
                self.catalog[doc] = str(target)
            if modified is not None:
                os.utime(self.catalog[doc], (modified, modified))
        return doc

    def session(self, doc: str) -> Session:
        path = self.catalog.get(doc)
        if path is None:
            raise LookupError("이 문서를 찾을 수 없습니다")
        # Two requests for a document nobody opened yet must end on one session; a second
        # one overwriting the first would leave its run going where no page can see it.
        with self.opening:
            if doc not in self.sessions:
                source = Path(path)
                self.sessions[doc] = Session(
                    source=source,
                    workspace=self.root / doc,
                    format=format_of(source),
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
            session.waiting = self.readers.submit(self._run, session)
        return session

    def _run(self, session: Session) -> None:
        self._steps(session, self._prepare, self._read, self._enrich)
        self._lighten(session)

    @staticmethod
    def _lighten(session: Session) -> None:
        """The pages' pictures let go once read: they are on disk, which is where the page
        is served from, and a folder of thousands would otherwise hold every one."""
        session.pages = tuple(replace(page, png=b"") for page in session.pages)

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
        except Exception:
            traceback.print_exc(file=sys.stderr)
            session.state, session.error = "error", UNREAD

    def _builder(self, session: Session) -> DocumentGraphBuilder:
        """The same graph builder the corpus build uses, over this document's
        pages as they were drawn once, under the slots every document shares."""
        return create_graph_builder(
            self.settings,
            renderer=_Replay(session.pages),
            vision=create_vision(self.settings, self.client),
            assets=session.store,
            limiter=self.vlm_slots,
            workers=VLM_CALLS_AT_ONCE,
        )

    def _read(self, session: Session) -> None:
        """Figures, then paragraphs, then the outline -- the builder's order. Each page's
        outline is applied the moment it lands, so the view grows while the rest is read."""
        builder = self._builder(session)
        session.state = "grouping"
        started = time.monotonic()
        session.extracted = builder.group_text(
            builder.read_figures(session.read(), session.source), session.source
        )
        session.state = "reading"
        session.pending = {page.index for page in session.pages}
        by_index = {page.index: page for page in session.pages}

        def landed(index: int, decisions: tuple[HierarchyDecision, ...] | None) -> None:
            # The pages not yet asked are not asked: a flowing document's come one after
            # another, and sheets' and slides' still queued are cancelled by the reader's pool;
            # only the ones already with the model finish.
            if session.stop is not None and session.stop.is_set():
                raise Cancelled
            unread = (index,) if decisions is None else ()
            derived = HierarchyDerivation(decisions=decisions or (), unread_pages=unread)
            self.apply(session, derived, by_index[index], _since(started))
            session.pending.discard(index)

        try:
            outlined = builder.read_outline(session.read(), session.source, on_page=landed)
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

    def _prepare(self, session: Session) -> None:
        session.workspace.mkdir(parents=True, exist_ok=True)
        # The crops go where the graph space's answers read them (`GraphRag._pictures`), not
        # under this reading's workspace: they outlive it, and they are named by content.
        session.store = LocalAssetStore(self.settings.asset_dir)
        # Drawn before this start and nothing that drew it changed: its pages as they were.
        kept_under = page_cache.key(content_hash(session.source), self.settings.viewer_directory)
        kept = page_cache.load(session.workspace, kept_under)
        if kept is not None:
            session.pages = kept
        else:
            with self.drawing:
                session.pages = self.renderer.render(session.source, session.format)
        bound = None if kept is None else page_cache.load_bound(
            session.workspace, kept_under, session.store)
        if bound is None:
            builder = self._builder(session)
            # Give tables their crop and let each own the pictures it encloses.
            bound = builder.bind(builder.extract(session.source, session.format), session.source)
            page_cache.save_bound(session.workspace, bound, kept_under)
        session.bound = session.extracted = bound
        session.page_of = pages_of(session.bound, session.pages, session.format)
        if kept is None:
            for page in session.pages:
                (session.workspace / f"page-{page.index:02d}.png").write_bytes(page.png)
            page_cache.save(session.workspace, session.pages, kept_under)

    @staticmethod
    def apply(
        session: Session, derived: HierarchyDerivation, page: RenderedPage, seconds: float
    ) -> None:
        """Apply one page's answer and record the parents it states on that page."""
        with session.lock:
            if page.index in derived.unread_pages:
                session.errors[page.index] = "VLM이 쓸 수 있는 답을 주지 않아 계층 없이 남음"
            else:
                session.errors.pop(page.index, None)
            was = session.read()
            # Pages of a flowing document land one at a time, each read against the graph as
            # it was when the reading began; a node an earlier page dropped is simply gone.
            known = {n.id for n in was.nodes}
            # A unit placed under one an earlier page dropped reads at the root, as the
            # pipeline reads it; a unit itself gone is not decided again.
            decisions = tuple(
                d if d.parent_id is None or d.parent_id in known
                else d.model_copy(update={"status": "root", "parent_id": None})
                for d in derived.decisions if d.subject_id in known
            )
            extracted = apply_hierarchy_decisions(was, decisions)
            session.extracted = extracted
            labels = {n.id: n.label for n in was.nodes}
            session.derived[page.index] = [
                {"node": labels[e.target_id][:70], "to": labels.get(e.source_id, "")[:50]}
                for e in extracted.edges
                if e.type is EdgeType.OUTLINE_PARENT
                and session.page_of.get(e.target_id) == page.index
            ]
            session.seconds[page.index] = seconds

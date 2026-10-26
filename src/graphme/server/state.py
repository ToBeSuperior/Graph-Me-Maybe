"""The server's state: each file through the pipeline (`Session`), a build into the graph
space (`CorpusBuild`), an export to Neo4j (`ExportJob`), and `Demo`, which holds them and
runs the work in threads. The HTTP routes over it are `app.py`; nothing here knows HTTP --
a refusal is `Busy`, `LookupError` or `ValueError`, and the route says what it means."""
from __future__ import annotations

import hashlib
import json
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
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from graphme.adapters.export.neo4j_admin import LocalNeo4jAdmin
from graphme.adapters.export.neo4j_import import write_import_bundle
from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.rendering.visual_binding import pages_of, with_geometry
from graphme.adapters.sources import Sources
from graphme.adapters.sources.box import SCHEME as BOX_SCHEME
from graphme.adapters.sources.box import BoxSource
from graphme.adapters.sources.box import Picked as BoxPicked
from graphme.adapters.sources.box import web_url as box_web_url
from graphme.adapters.sources.box_account import ACCOUNT as BOX_ACCOUNT
from graphme.adapters.sources.box_account import BoxAccount
from graphme.adapters.sources.box_drive import box_drive_root, listing
from graphme.adapters.sources.drive import SCHEME as DRIVE_SCHEME
from graphme.adapters.sources.drive import DriveSource
from graphme.adapters.sources.drive import web_url as drive_web_url
from graphme.adapters.sources.google import Client, GoogleAccount, Picked, vault
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.adapters.storage.graph_rag import GraphRag
from graphme.adapters.storage.neo4j_space import Neo4jGraphSpace
from graphme.api import GraphMe
from graphme.application.corpus_build import (
    BuildReport,
    BuildStep,
    build_graph,
    content_hash,
    relocated,
    with_origin,
)
from graphme.application.document_graph import DocumentGraphBuilder
from graphme.application.embedded_documents import (
    embedded_in,
    name_part,
    safe_name,
    shown_name,
)
from graphme.application.hierarchy_decisions import apply_hierarchy_decisions
from graphme.application.ingest import SUFFIX_FORMATS, format_of
from graphme.application.progress import Progress
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.config import Settings
from graphme.domain.enums import FLOW_FORMATS, DocumentFormat, EdgeType
from graphme.domain.ids import sha256_bytes
from graphme.domain.models import ExtractedDocument, HierarchyDecision, HierarchyDerivation
from graphme.ports.sources import SourceStore
from graphme.runtime import create_graph_builder, create_neo4j_admin, create_vision
from graphme.server.jobs import (
    BUILDING,
    CANCELLED,
    EXPORTING,
    FINISHED,
    GONE,
    IN_LOG,
    STARTABLE,
    STARTING,
    STEP_STATES,
    UNDRAWN,
    UNREAD,
    Busy,
    Cancelled,
    CorpusBuild,
    ExportJob,
    GraphStarting,
    Kept,
    Session,
    Undrawn,
    _mark_failed,
)
from graphme.server.search_models import SearchModels

VLM_CALLS_AT_ONCE = 24
"""Model calls in flight across every document, each reading and each settling call counted
on its own. Lower it when the endpoint's rate limit turns calls away; a call turned away is
retried, so too high costs time rather than pages."""
READERS = 16
"""Documents read side by side; their model calls share VLM_CALLS_AT_ONCE besides."""
FEWEST_WORKERS = 4
"""Pages a document asks at once however many are read beside it: its share of the calls in
flight, but never so few that one slow page holds the rest of its pages back."""

def _stamp(path: str | Path) -> tuple[int, int] | None:
    """A file's size and modification time, or None when it is not there."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_size, status.st_mtime_ns


PREVIEWS_KEPT = 24


def kept_from(records: dict[str, Any], fmt: DocumentFormat
              ) -> tuple[Kept, tuple[RenderedPage, ...]] | None:
    """A document of the space shown by what its build wrote (`with_geometry`), or None when
    it was built before that and has to be drawn and bound again."""
    root = next((n for n in records.get("nodes", ()) if "Document" in n["labels"]), None)
    said = root["properties"].get("meta_pages") if root is not None else None
    if not said:
        return None
    drawn = json.loads(said) if isinstance(said, str) else said
    pages = tuple(RenderedPage(index=int(p["index"]), width=int(p["width"]),
                               height=int(p["height"]), png=b"", label=p.get("name"))
                  for p in drawn)
    page_of: dict[str, int] = {}
    boxes: dict[str, tuple[int, list[int]]] = {}
    crops: dict[str, str] = {}
    for node in records["nodes"]:
        properties = node["properties"]
        if isinstance(page := properties.get("meta_page"), int):
            page_of[node["id"]] = page
            if isinstance(box := properties.get("meta_box"), list) and len(box) == 4:
                boxes[node["id"]] = (page, [int(v) for v in box])
        if isinstance(crop := properties.get(f"meta_{CROP_ASSET_KEY}"), str):
            crops[node["id"]] = crop
    return Kept(records, page_of, boxes, crops), pages


def _forget_kept_pages(root: Path) -> None:
    """What earlier versions kept of every document drawn -- its page images, their measures
    and its bound graph -- gone: a page is drawn when it is looked at."""
    for kept in (*root.glob("*/page-*.png"), *root.glob("*/pages.json"),
                 *root.glob("*/bound.json")):
        kept.unlink(missing_ok=True)


class _Lazy:
    """A source made when first asked for: a cloud drive is signed in only when one of its
    files is needed."""

    def __init__(self, make: Callable[[], SourceStore], web_url: Callable[[str], str]) -> None:
        self._make = make
        self._web_url = web_url

    def open_copy(self, origin: str) -> AbstractContextManager[Path]:
        try:
            store = self._make()
        except LookupError as error:  # not set up, or not signed in
            raise FileNotFoundError(f"{origin}: {error}") from error
        return store.open_copy(origin)

    def web_url(self, origin: str) -> str | None:
        return self._web_url(origin)


@dataclass
class _Replay:
    """Hands back pages already rendered, so nothing is drawn twice."""

    pages: tuple[RenderedPage, ...]

    def render(self, source: Path, document_format: DocumentFormat) -> tuple[RenderedPage, ...]:
        return self.pages


def _standing(
    was: ExtractedDocument, decisions: Sequence[HierarchyDecision]
) -> tuple[HierarchyDecision, ...]:
    """The decisions that still have a unit to decide. A unit placed under one an earlier page
    dropped reads at the root, as the pipeline reads it; a unit itself gone is not decided
    again."""
    known = {n.id for n in was.nodes}
    return tuple(
        d if d.parent_id is None or d.parent_id in known
        else d.model_copy(update={"status": "root", "parent_id": None})
        for d in decisions if d.subject_id in known
    )


def _since(started: float) -> float:
    return round(time.monotonic() - started, 1)


# What failed is said on the page plainly; what it was -- with this machine's paths in it --
# is in the server's log, printed where it happened.
SPACE_WAIT = 2.0
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
        self.search_models = SearchModels(self)
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
        self.putting: set[str] = set()
        """The running build's documents not in the space yet: one read and not put yet is
        waiting to be put, not left out."""
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
        self._running = 0
        """Documents being read now, which share the calls in flight (`_workers`)."""
        self._counting = threading.Lock()
        self.hashes: dict[str, tuple[tuple[int, int] | None, str]] = {}
        """Each document's file hash, beside the size and time it was worked out at."""
        self.previews: OrderedDict[tuple[str, int, tuple[int, int] | None], bytes] = OrderedDict()
        """Pages drawn for a reader, the latest PREVIEWS_KEPT: nothing is kept across starts."""
        self.previewing = threading.Lock()
        self.sources = Sources()
        self._google: tuple[Client, GoogleAccount] | None = None
        self.origins: dict[str, tuple[str, dict[str, str | None]]] = {}
        """A file fetched from a cloud drive for a build: its address there, and what is
        recorded about it."""
        self.sources.add(DRIVE_SCHEME, _Lazy(self._drive, drive_web_url))
        self.sources.add(BOX_SCHEME, _Lazy(self._box_source, box_web_url))
        self._box: tuple[Client, BoxAccount] | None = None
        """Where originals are read from: this machine, and a connected cloud drive."""
        if root is not None:
            _forget_kept_pages(self.root)
        self.build = CorpusBuild()
        self.export = ExportJob()
        self.exporting = threading.Lock()
        self.dump_path = self.root / "neo4j.dump"
        self.dump_path.unlink(missing_ok=True)  # made by an earlier start, not this one
        self.building = threading.Lock()
        self.build_stop = threading.Event()
        """Set to stop the build under way -- a new one each build, so a cancel never outlives
        it: documents not yet read are passed over, what is in the graph stays searchable."""
        self.opening_space = threading.Lock()
        self._opener: threading.Thread | None = None
        self._starting_opener = threading.Lock()
        self._open_failed: Exception | None = None

    def reconfigure(self, settings: Settings) -> None:
        """New keys or model, taken up from the next reading and the next question. The graph
        space is kept: what it is reached by is not among what changes."""
        searched_with = self.settings.embedding_provider
        self.settings = settings
        self.graph.settings = settings
        with self.opening_space:
            self.graph.forget_rag()
        if settings.embedding_provider != searched_with:
            self.search_models.chosen_changed()

    def space(self, *, wait: float = -1) -> Neo4jGraphSpace:
        """The graph space every built document is put in, opened on first use. A build waits
        for it however long; a page's request (`wait`) only so long."""
        # The first use may start the local Neo4j; the page asks for the list and the map at once.
        if wait < 0:
            with self.opening_space:
                return self.graph.graph_space()
        if (opened := self.graph.opened_space) is not None:
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

    def _open_behind(self) -> None:
        try:
            with self.opening_space:
                self.graph.graph_space()
        except Exception as error:  # noqa: BLE001 -- said to the next request
            self._open_failed = error

    def admin(self) -> LocalNeo4jAdmin:
        """`neo4j-admin` from the app's own Neo4j (`neo4j_admin.py`)."""
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
                # A folder name is one folder: no separator, no way out of the zip.
                where = name_part(folder, "files")
                if not path:
                    missing.append(f"{document_id}\tnot in the graph")
                    continue
                stem, suffix = os.path.splitext(shown_name(Path(path).name))
                name, n = f"{where}/{stem}{suffix}", 1
                while name in taken:  # two files of one name in one folder: both kept
                    n += 1
                    name = f"{where}/{stem} ({n}){suffix}"
                try:
                    with self.sources.open_copy(path) as copy:
                        out.write(copy, name)
                except (FileNotFoundError, LookupError):
                    # Named as a reader can find it: where it was for, and where it was.
                    missing.append(f"{where}/{stem}{suffix}\tnot found at {path}")
                    continue
                taken.add(name)
            if missing:
                out.writestr("missing-files.txt", "These could not be added:\n"
                             + "\n".join(missing) + "\n")
        return archive

    def start_export(
        self, kind: str, docs: list[str] | None, aura: dict[str, str] | None = None
    ) -> ExportJob:
        """A `.dump` of the space, and for an upload that dump pushed to Aura, in the
        background. `aura` -- address, user, password -- lives only in the thread."""
        with self.exporting:
            if self.export.state in EXPORTING:
                raise Busy("an export is already running")
            job = self.export = ExportJob(
                id=uuid.uuid4().hex, kind=kind, state="dumping", message="making the dump")
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
                job.state, job.message = "uploading", "uploading to Aura"
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
            return self.graph.rag()

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
        found = kept_from(space.document_graph(document_id), session.format)
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

    def preview(self, doc: str, index: int) -> bytes:
        """Page `index` of a document drawn for a reader, now: no page is kept for every
        document, and only the latest PREVIEWS_KEPT for this start."""
        session = self.session(doc)
        key = (doc, index, session.stamp)
        with self.previewing:
            if key in self.previews:
                self.previews.move_to_end(key)
                return self.previews[key]
        try:
            original = session.origin or str(session.source)
            with self.sources.open_copy(original) as source, self.drawing:
                try:
                    png = self.renderer.preview(source, session.format, index)
                except Exception as error:
                    raise Undrawn(UNDRAWN) from error
        except FileNotFoundError as error:
            raise LookupError(GONE) from error
        with self.previewing:
            self.previews[key] = png
            while len(self.previews) > PREVIEWS_KEPT:
                self.previews.popitem(last=False)
        return png

    # Google Drive --------------------------------------------------------------------------

    def google(self) -> GoogleAccount:
        """The Google account the user signs in with, through their own OAuth client."""
        client_id = self.settings.google_client_id
        if not client_id:
            raise LookupError("Google Drive is not set up -- put in your OAuth client (Desktop "
                              "app) in Keys and model")
        client = Client(id=client_id, secret=self.settings.google_client_secret)
        with self.opening:
            if self._google is None or self._google[0] != client:
                self._google = (client, GoogleAccount(
                    client, self.client, vault(self.root / "google.token")))
            return self._google[1]

    @property
    def picked(self) -> list[str]:
        """The Drive files the user picked, in the order picked."""
        try:
            said = json.loads((self.root / "google.json").read_text(encoding="utf-8"))
            return [str(i) for i in said.get("picked", [])]
        except (OSError, ValueError, AttributeError):
            return []

    def google_state(self) -> dict[str, Any]:
        configured = bool(self.settings.google_client_id)
        return {"configured": configured,
                "connected": configured and self.google().connected(),
                "picked": len(self.picked)}

    def google_picked(self, picked: Picked) -> int:
        """`picked` kept beside what was picked before; how many are kept now."""
        kept = list(dict.fromkeys([*self.picked, *picked.file_ids]))
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "google.json").write_text(json.dumps({"picked": kept}), encoding="utf-8")
        return len(kept)

    def google_disconnect(self) -> None:
        self.google().disconnect()
        (self.root / "google.json").unlink(missing_ok=True)

    def open_path(self, path: str) -> str:
        """A document on this machine, opened by its path, and its id. One kept under
        `uploads/<id>/` -- uploaded, or carried in another document -- is that id: opened
        again after a restart it is the same document, in the same folder."""
        kept = Path(path)
        uploads = self.root / "uploads"
        doc = kept.parent.name if kept.parent.parent == uploads else self.doc_id(path)
        with self.opening:
            self.catalog.setdefault(doc, path)
        return doc

    def build_corpus(
        self, docs: list[str], source: str, *, put: bool = True, embed: bool = True,
        find: Callable[[CorpusBuild], list[str]] | None = None,
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
        threading.Thread(target=self._build_then_let_go, args=(docs, put, embed, find),
                         daemon=True).start()
        return self.build

    def _build_then_let_go(self, docs: list[str], put: bool, embed: bool,
                           find: Callable[[CorpusBuild], list[str]] | None = None) -> None:
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
                if not docs:
                    build.state = "error"
                    build.error = "no readable document (xlsx, pptx, docx, pdf)"
                    return
            self._build_corpus(docs, put, embed)
        finally:
            build.ended_at = time.monotonic()
            for doc in docs:
                if doc in self.origins:
                    shutil.rmtree(Path(self.catalog.get(doc, "")).parent, ignore_errors=True)

    def build_google(self) -> CorpusBuild:
        """The picked Drive files read into the space: what the space holds already, by the
        content Drive states (`sha256Checksum`), is passed over without being fetched; the rest
        is fetched for the build and removed after it. A file changed since is read again and
        replaces its earlier reading."""
        drive = self._drive()
        found, lost = drive.documents(self.picked)
        held = self.space().contents([f.sha256 for f in found if f.sha256])
        docs: list[str] = []
        for wanted in (f for f in found if f.sha256 not in held):
            folder = self.root / "drive" / wanted.id
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            with drive.open_copy(wanted.origin) as copy:
                kept = folder / copy.name
                shutil.copyfile(copy, kept)
            doc = self.open_path(str(kept))
            self.origins[doc] = (wanted.origin, {
                "drive_file_id": wanted.id, "drive_revision": wanted.revision,
                "web_url": wanted.web_url or drive.web_url(wanted.origin)})
            docs.append(doc)
        build = self.build_corpus(docs, "Google Drive")
        build.known += len(found) - len(docs)
        build.failed += [f"{file_id}: no longer readable in Google Drive" for file_id in lost]
        return build

    def _drive(self) -> DriveSource:
        return DriveSource(self.client, self.google().access_token, self.root / "drive-copies")

    # Box ------------------------------------------------------------------------------------

    def box(self) -> BoxAccount:
        """The Box account the user signs in with, through their own Box app."""
        client_id, secret = self.settings.box_client_id, self.settings.box_client_secret
        if not client_id or not secret:
            raise LookupError("Box is not set up -- put in your Box app's client ID and secret "
                              "in Keys and model")
        client = Client(id=client_id, secret=secret)
        with self.opening:
            if self._box is None or self._box[0] != client:
                self._box = (client, BoxAccount(
                    client, self.client, vault(self.root / "box.token", BOX_ACCOUNT)))
            return self._box[1]

    def _box_kept(self) -> dict[str, Any]:
        try:
            said = json.loads((self.root / "box.json").read_text(encoding="utf-8"))
            return said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            return {}

    def _box_keep(self, kept: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "box.json").write_text(json.dumps(kept), encoding="utf-8")

    @property
    def box_picked(self) -> list[BoxPicked]:
        """The Box folders and files the user picked, in the order picked."""
        return [BoxPicked(str(i["type"]), str(i["id"]), str(i["name"]))
                for i in self._box_kept().get("picked", [])
                if isinstance(i, dict) and i.get("type") in ("folder", "file")]

    def box_state(self) -> dict[str, Any]:
        configured = bool(self.settings.box_client_id and self.settings.box_client_secret)
        return {"configured": configured, "connected": configured and self.box().connected(),
                "picked": [{"type": p.type, "id": p.id, "name": p.name}
                           for p in self.box_picked]}

    def box_pick(self, items: Sequence[BoxPicked]) -> dict[str, Any]:
        """`items` are what is picked now, in place of what was."""
        kept = self._box_kept()
        kept["picked"] = [{"type": i.type, "id": i.id, "name": i.name}
                          for i in dict.fromkeys(items)]
        self._box_keep(kept)
        return self.box_state()

    def box_folder(self, folder_id: str) -> dict[str, Any]:
        """A Box folder a level at a time, each item marked when it is picked itself."""
        listed = self._box_source().folder(folder_id)
        picked = {(p.type, p.id) for p in self.box_picked}
        for item in listed["items"]:
            item["picked"] = (item["type"], item["id"]) in picked
        return listed

    def box_disconnect(self) -> None:
        self.box().disconnect()
        (self.root / "box.json").unlink(missing_ok=True)

    def build_box(self) -> CorpusBuild:
        """The documents in what was picked in Box, read into the space. A file is known by
        the version Box states: one whose version was read before and whose content the
        space still holds is passed over without being fetched; the rest is fetched for the
        build and removed after it."""
        box = self._box_source()
        found, lost = box.documents(self.box_picked)
        kept = self._box_kept()
        versions: dict[str, str] = dict(kept.get("versions", {}))
        """Box version id -> the SHA-256 of its content, as read here."""
        held = self.space().contents([versions[f.version] for f in found
                                      if f.version in versions])
        docs: list[str] = []
        for wanted in found:
            if wanted.version in versions and versions[wanted.version] in held:
                continue
            folder = self.root / "box" / wanted.id
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            with box.open_copy(wanted.origin) as copy:
                copied = folder / copy.name
                shutil.copyfile(copy, copied)
            if wanted.version and (digest := content_hash(copied)):
                versions[wanted.version] = digest
            doc = self.open_path(str(copied))
            self.origins[doc] = (wanted.origin, {
                "box_file_id": wanted.id, "box_version": wanted.version,
                "web_url": box.web_url(wanted.origin)})
            docs.append(doc)
        kept["versions"] = versions
        self._box_keep(kept)
        build = self.build_corpus(docs, "Box")
        build.known += len(found) - len(docs)
        build.failed += [f"{name}: no longer readable in Box" for name in lost]
        return build

    @staticmethod
    def box_drive() -> Path | None:
        """Box Drive's folder on this machine, when it is here."""
        return box_drive_root(Path.home(), platform=sys.platform, environ=os.environ)

    def box_drive_connected(self) -> bool:
        """Whether the user said to read Box Drive: asked once, then kept for every start."""
        try:
            said = json.loads((self.root / "box-drive.json").read_text(encoding="utf-8"))
            return bool(said.get("connected"))
        except (OSError, ValueError, AttributeError):
            return False

    def box_drive_connect(self, connected: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "box-drive.json").write_text(json.dumps({"connected": connected}),
                                                  encoding="utf-8")

    def box_drive_state(self) -> dict[str, Any]:
        root = self.box_drive()
        return {"found": root is not None, "root": str(root) if root else None,
                "connected": root is not None and self.box_drive_connected()}

    def box_drive_folder(self, relative: str) -> dict[str, Any]:
        """A folder of Box Drive's, a level at a time; LookupError when Box Drive is not
        here, Busy until the user has said to read it."""
        root = self.box_drive()
        if root is None:
            raise LookupError("Box Drive is not on this machine")
        if not self.box_drive_connected():
            raise Busy("Box Drive is not connected -- connect it first")
        return listing(root, relative)

    def _box_source(self) -> BoxSource:
        return BoxSource(self.client, self.box().access_token, self.root / "box-copies")

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
        if known:
            relocated(self.space(), {self.catalog[doc]: self.content_hash(doc) for doc in known
                                     if doc in self.catalog})
        build.carried = sum(doc not in known for doc in docs[len(given):])
        docs = [doc for doc in docs if doc not in known]
        build.known, build.total = len(known), len(docs)
        for doc in docs:
            self.start(doc, stop)  # every document reads side by side, under the shared slots

        def finished() -> int:
            # The documents are read side by side and finish in any order, and are put in the
            # order they finish, so how far it is is how many are read.
            return sum(self.sessions[doc].state in FINISHED for doc in docs)

        def share() -> int:
            """How far the reading is: each document read is whole, each being read as far as
            its questions answered, each waiting for a reader not begun."""
            def one(doc: str) -> float:
                session = self.sessions[doc]
                if session.state in FINISHED:
                    return 1.0
                done, planned = session.progress.counts()
                return done / planned if planned else 0.0
            return int(100 * sum(one(doc) for doc in docs) / len(docs)) if docs else 100

        def counted() -> int:
            build.percent = max(build.percent or 0, share())  # an estimate replaced: no step back
            return finished()

        def in_turn(paths: Sequence[Path]) -> Iterator[Path]:
            """Each document once it is read; when a cancel or a reading that never ends stops
            the wait, the rest, which `process` then says were not read."""
            left = list(paths)
            deadline = time.monotonic() + READ_TIMEOUT_SECONDS
            while left:
                read = [p for p in left if self.sessions[p.name].state in FINISHED]
                if not read and (stop.is_set() or time.monotonic() > deadline):
                    read = left
                if not read:
                    build.done = counted()
                    time.sleep(1)
                    continue
                left = [p for p in left if p not in read]
                deadline = time.monotonic() + READ_TIMEOUT_SECONDS
                yield from read

        def process(handle: Path) -> ExtractedDocument:
            session = self.sessions[handle.name]
            if session.state not in FINISHED:
                if stop.is_set():
                    raise RuntimeError(f"{session.source.name}: {CANCELLED}")
                raise RuntimeError(f"{session.source.name}: not read in time")
            if session.state != "done" or session.extracted is None:
                raise RuntimeError(f"{Path(session.source).name}: {session.error}")
            self.putting.discard(handle.name)  # taken by the space now, not waiting for it
            if handle.name in self.origins:
                return with_origin(session.extracted, *self.origins[handle.name])
            return session.extracted

        def step(name: BuildStep, done: int, total: int) -> None:
            build.state, build.total = STEP_STATES[name], total
            build.done = done if name == BuildStep.LINK else counted()

        def read_only() -> BuildReport:
            failed = []
            for path in in_turn([Path(doc) for doc in docs]):
                try:
                    process(path)
                except RuntimeError as error:
                    failed.append((path, str(error)))
            build.done = counted()
            return BuildReport(processed=len(docs) - len(failed), failed=tuple(failed), links=0)

        self.putting = set(docs) if put else set()
        try:
            report = build_graph(
                [Path(doc) for doc in docs], process=process, space=self.space(), on_step=step,
                in_turn=in_turn,
            ) if put else read_only()
        except Exception:  # noqa: BLE001 -- the build's state says what happened
            traceback.print_exc(file=sys.stderr)
            build.state, build.error = "error", f"the graph could not be built {IN_LOG}"
            return
        finally:
            self.putting = set()
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
        """The SHA-256 of a document's file -- what the graph space knows it by -- read again
        only when the file's size or time says it changed."""
        stamp = _stamp(self.catalog[doc])
        kept = self.hashes.get(doc)
        if kept is None or kept[0] != stamp:
            digest = content_hash(Path(self.catalog[doc]))
            if digest is None:
                return None
            kept = self.hashes[doc] = (stamp, digest)
        return kept[1]

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
            raise ValueError(f"unsupported format ({', '.join(SUFFIX_FORMATS)})")
        if not content:
            raise ValueError("the file is empty")
        digest = sha256_bytes(content)
        doc = digest[:10]
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
            self.hashes[doc] = (_stamp(self.catalog[doc]), digest)  # known: no second read
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
            stale = open_ is not None and open_.kept is None and open_.stamp != _stamp(path) and (
                open_.state in (*STARTABLE, "done"))
            if open_ is None or stale:
                source = Path(path)
                self.sessions[doc] = Session(
                    source=source,
                    workspace=self.root / doc,
                    format=format_of(source),
                    origin=self.origins[doc][0] if doc in self.origins else path,
                    stamp=_stamp(path),
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
        with self._counting:
            self._running += 1
        session.started_at, session.ended_at = time.monotonic(), None
        try:
            self._steps(session, self._prepare, self._read, self._enrich, self._drawn)
        finally:
            session.ended_at = time.monotonic()
            with self._counting:
                self._running -= 1
        self._lighten(session)

    def _workers(self, *, running: int | None = None) -> int:
        """How many pages a document asks at once: the calls in flight shared among the
        documents being read now. A thread waiting for a slot does nothing; sixteen documents
        each asking two dozen pages kept hundreds of them waiting. A document started while
        few others run keeps its larger share when more start after it."""
        # ponytail: shared at start, not rebalanced; a shared page pool if that ever matters
        now = self._running if running is None else running
        return max(FEWEST_WORKERS, VLM_CALLS_AT_ONCE // max(1, now))

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
            session.state, session.error = "error", UNDRAWN
        except Exception:
            traceback.print_exc(file=sys.stderr)
            session.state, session.error = "error", UNREAD

    def _builder(self, session: Session) -> DocumentGraphBuilder:
        """The same graph builder the corpus build uses, over this document's
        pages as they were drawn once, under the slots every document shares."""
        return create_graph_builder(
            self.settings,
            renderer=_Replay(session.pages),
            vision=create_vision(self.settings, self.client, limiter=self.vlm_slots),
            assets=session.store,
            workers=self._workers(),
            progress=session.progress,
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
                    grouped[0], _standing(grouped[0], landed_so_far))
            self.apply(session, derived, by_index[index], _since(started))
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

    def _prepare(self, session: Session) -> None:
        session.workspace.mkdir(parents=True, exist_ok=True)
        # The crops go where the graph space's answers read them (`GraphRag._pictures`), not
        # under this reading's workspace: they outlive it, and they are named by content.
        session.store = LocalAssetStore(self.settings.asset_dir)
        with self.sources.open_copy(session.origin or str(session.source)) as source:
            with self.drawing:
                try:
                    session.pages = self.renderer.render(source, session.format)
                except Exception as error:
                    raise Undrawn(UNDRAWN) from error
            builder = self._builder(session)
            # Give tables their crop and let each own the pictures it encloses.
            bound = builder.bind(builder.extract(source, session.format), source)
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
            extracted = apply_hierarchy_decisions(was, _standing(was, derived.decisions))
            session.extracted = extracted
            labels = {n.id: n.label for n in was.nodes}
            session.derived[page.index] = [
                {"node": labels[e.target_id][:70], "to": labels.get(e.source_id, "")[:50]}
                for e in extracted.edges
                if e.type is EdgeType.OUTLINE_PARENT
                and session.page_of.get(e.target_id) == page.index
            ]
            session.seconds[page.index] = seconds

"""What every layer of the server's state (`state.Demo`) stands on: the state itself,
made at start, and the few things every layer asks of it -- the workspaces, mail and Box
accounts, and the files the server holds. Each layer above is a module of its own and sees
the layers under it."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx

from graphme import processes
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.sources import Sources
from graphme.adapters.sources.box import SCHEME as BOX_SCHEME
from graphme.adapters.sources.box import BoxSource
from graphme.adapters.sources.box import web_url as box_web_url
from graphme.adapters.sources.box_account import ACCOUNT as BOX_ACCOUNT
from graphme.adapters.sources.box_account import BoxAccount
from graphme.adapters.sources.google_sign_in import GoogleSignIn
from graphme.adapters.sources.microsoft_sign_in import MicrosoftSignIn
from graphme.adapters.sources.sign_in import Client
from graphme.application.embedded_documents import safe_name
from graphme.application.ingest import SUFFIX_FORMATS
from graphme.config import Settings
from graphme.domain.ids import sha256_bytes
from graphme.server import state
from graphme.server.jobs import (
    Busy,
    CorpusBuild,
    ExportJob,
    Session,
)
from graphme.server.mail_accounts import MailAccounts
from graphme.server.mail_sync import MailSync
from graphme.server.search_models import SearchModels
from graphme.server.threads import ThreadStore
from graphme.server.watch import Watcher
from graphme.server.workspaces import Workspaces

if TYPE_CHECKING:
    from graphme.server.state import Demo


class DemoBase:
    """The server's state, made at start, and the files it holds."""

    def __init__(
        self, settings: Settings, renderer: PageRenderer, root: Path | None = None
    ) -> None:
        # The app's own Neo4j keeps every workspace's graph; the one open when the app ended opens.
        self.workspaces = Workspaces(settings.local_neo4j) if settings.local_neo4j else None
        if self.workspaces is not None:
            settings = state._in_workspace(settings, self.workspaces, self.workspaces.active)
        # The same settings build-graph reads: model, endpoint, readings. A demo that picked its
        # own defaults showed an outline the pipeline would not have produced.
        self.settings = settings
        self.renderer = renderer
        self.search_models = SearchModels(cast("Demo", self))
        if root is None:
            # A folder of its own, gone with it: a demo made for a moment (a test) leaves none.
            self.root = Path(tempfile.mkdtemp(prefix=state.ROOT_PREFIX))
            weakref.finalize(self, shutil.rmtree, self.root, True)
        else:
            # The same folder each start: an uploaded file the graph points to is still there.
            self.root = root
            self.root.mkdir(parents=True, exist_ok=True)
        self._mail: MailAccounts | None = None
        self._mail_sync: MailSync | None = None
        self._mail_stop = threading.Event()
        self._mail_thread: threading.Thread | None = None
        self._watcher: Watcher | None = None
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self.graph = state.GraphMe(settings)
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
        self.vlm_slots = threading.Semaphore(state.VLM_CALLS_AT_ONCE)
        self.opening = threading.Lock()
        # One pool of connections to the endpoint for every reading, not a new one per page.
        self.client = httpx.Client(timeout=600.0)
        # Every image and every poll looks a document up here: every document a folder brought.
        self.catalog: dict[str, str] = {}
        self.open_file: Callable[[Path], None] = processes.open_file
        """How a document's original is opened on this machine (`open_original`)."""
        self.agent_token: str | None = None
        """The token the agent wrote in `server.json`: a client sending it is let in."""
        self.readers = ThreadPoolExecutor(max_workers=state.READERS, thread_name_prefix="read")
        self.reading_at_once = state.READERS
        """Documents read at once by `readers`: a build lets in twice as many, no more."""
        self.hashes: dict[str, tuple[tuple[int, int] | None, str]] = {}
        """Each document's file hash, beside the size and time it was worked out at."""
        self.previews: OrderedDict[tuple[str, int, tuple[int, int] | None], bytes] = OrderedDict()
        """Pages drawn for a reader, the latest PREVIEWS_KEPT: nothing is kept across starts."""
        self.previewing = threading.Lock()
        self.drawing_around: set[str] = set()
        """Documents whose pages around one looked at are being drawn behind it."""
        self.preview_dir = self.root / "previews"
        """The pages drawn for readers, kept on disk under `preview_cache_mb`."""
        self.viewed: dict[str, tuple[Path, float]] = {}
        """Files a cloud drive downloaded to be drawn, and when each was last looked at."""
        self.viewing = threading.Lock()
        self._watching: threading.Thread | None = None
        self.sources = Sources()
        self.origins: dict[str, tuple[str, dict[str, str | None]]] = {}
        """A file fetched from a cloud drive for a build: its address there, and what is
        recorded about it."""
        self.sources.add(BOX_SCHEME, state._Lazy(self._box_source, box_web_url))
        self._box: tuple[Client, BoxAccount] | None = None
        self._google: GoogleSignIn | None = None
        self._microsoft: MicrosoftSignIn | None = None
        """Where originals are read from: this machine, and a connected cloud drive."""
        if root is not None:
            state._forget_kept_pages(self.root)
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
        self._sides: dict[str, tuple[object, state.GraphMe]] = {}
        """Each workspace read beside the open one (`reader`): the open graph's space it was
        held from, and its graph over that connection."""
        self.siding = threading.Lock()
        self._moves_tried = False
        self._moving = False  # the graph is open but not all of it moved in yet
        self.unmoved: dict[str, str] = {}
        """Workspaces whose graph could not be moved from their folder into the one Neo4j at
        this start, and why: still in their folder, tried again at the next start."""

    def workspace_registry(self) -> Workspaces:
        if self.workspaces is None:
            raise Busy("workspaces are the app's own graph DB's -- this server uses another")
        return self.workspaces

    def threads(self, workspace: str | None = None) -> ThreadStore:
        """The conversations kept for `workspace` (the open one when None): beside its crops,
        in its folder in the home, the home's own graph's in `<home>/.graphme/threads`.
        LookupError for a workspace there is none of."""
        settings = self.settings
        if workspace is not None and self.workspaces is not None:
            self.workspaces.item(workspace)
            settings = state._in_workspace(settings, self.workspaces, workspace)
        return ThreadStore(settings.asset_dir.parent / "threads")

    def thread_store(self, thread_id: str) -> ThreadStore:
        """The store holding `thread_id`, whichever workspace it asks; KeyError for none."""
        names = [self.settings.workspace]
        if self.workspaces is not None:
            names += [item["id"] for item in self.workspaces.listed()["items"]]
        for name in dict.fromkeys(names):
            store = self.threads(name)
            if store.has(thread_id):
                return store
        raise KeyError(thread_id)

    def mail_accounts(self) -> MailAccounts:
        spaces = self.workspace_registry()
        if self._mail is None:
            self._mail = MailAccounts(spaces.home, spaces, google=self.google_sign_in(),
                                      microsoft=self.microsoft_sign_in(), http=self.client)
        return self._mail

    def microsoft_sign_in(self) -> MicrosoftSignIn:
        """Signing in to read Outlook mail, personal or work, through the project's Microsoft
        app registration (or the one `GRAPHME_MICROSOFT_CLIENT_ID` names)."""
        with self.opening:
            if self._microsoft is None:
                self._microsoft = MicrosoftSignIn(self.settings.microsoft_client_id, self.client)
            return self._microsoft

    def google_sign_in(self) -> GoogleSignIn | None:
        """Signing in to read Gmail, through the project's Google client; None in a build
        without one."""
        client_id, secret = self.settings.google_client_id, self.settings.google_client_secret
        if not client_id:
            return None
        with self.opening:
            if self._google is None:
                self._google = GoogleSignIn(Client(id=client_id, secret=secret), self.client)
            return self._google

    @property
    def mail_sync(self) -> MailSync:
        if self._mail_sync is None:
            self._mail_sync = MailSync(self.mail_accounts(), cast("Demo", self))
        return self._mail_sync

    def start_mail_sync(self) -> None:
        """The agent that reads new mail, once per server; not for a server without workspaces."""
        if self.workspaces is None or self._mail_thread is not None:
            return
        self._mail_stop = threading.Event()
        self._mail_thread = threading.Thread(
            target=self.mail_sync.run, args=(self._mail_stop,), daemon=True)
        self._mail_thread.start()

    def stop_mail_sync(self) -> None:
        self._mail_stop.set()
        self._mail_thread = None

    @property
    def watcher(self) -> Watcher:
        """The chosen targets' automatic builds; Busy for a server without workspaces."""
        self.workspace_registry()
        if self._watcher is None:
            self._watcher = Watcher(cast("Demo", self))
        return self._watcher

    def start_watching(self) -> None:
        """The chosen targets looked at, once per server; not for one without workspaces."""
        if self.workspaces is None or self._watch_thread is not None:
            return
        self._watch_stop = threading.Event()
        self._watch_thread = threading.Thread(
            target=self.watcher.run, args=(self._watch_stop,), daemon=True)
        self._watch_thread.start()

    def stop_watching(self) -> None:
        self._watch_stop.set()
        self._watch_thread = None

    def not_in_mail_workspace(self) -> None:
        """Busy when the workspace open reads its mailbox only: no folder, drive or upload, and no
        document deleted nor the graph emptied by hand -- the mailbox alone says what it holds."""
        spaces = self.workspaces
        if spaces is not None and spaces.item(spaces.active)["kind"] == "mail":
            raise Busy("a mail workspace reads its own mailbox only")

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
                    client, self.client, state.vault(self.root / "box.token", BOX_ACCOUNT)))
            return self._box[1]

    def _box_source(self) -> BoxSource:
        return BoxSource(self.client, self.box().access_token, self.root / "box-copies")

    def drop(self, docs: Iterable[str]) -> None:
        """The server's own copies of these files let go: their readings, files and hashes."""
        with self.opening:
            for doc in list(docs):
                self.sessions.pop(doc, None)
                self.catalog.pop(doc, None)
                self.hashes.pop(doc, None)

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
            self.hashes[doc] = (state._stamp(self.catalog[doc]), digest)  # known: no second read
        return doc

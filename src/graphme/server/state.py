"""The server's state: each file through the pipeline (`Session`), a build into the graph
space (`CorpusBuild`), an export to Neo4j (`ExportJob`), and `Demo`, which holds them and
runs the work in threads. The HTTP routes over it are `app.py`; nothing here knows HTTP --
a refusal is `Busy`, `LookupError` or `ValueError`, and the route says what it means.

What every layer of `Demo` reads -- the constants, the helpers, and the functions a test
stands in for -- is this module's, read through it (`state.NAME`) by the layers."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.sources.cloud_files import evict, online_only
from graphme.adapters.sources.imap_mail import MailRefused
from graphme.adapters.sources.sign_in import vault
from graphme.adapters.storage.workspace_move import copy_graph
from graphme.api import GraphMe
from graphme.application.corpus_build import content_hash
from graphme.application.embedded_documents import embedded_in
from graphme.application.visual_crops import CROP_ASSET_KEY
from graphme.config import Settings
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import ExtractedDocument, HierarchyDecision
from graphme.ports.sources import SourceStore
from graphme.runtime import (
    create_graph_builder,
    create_neo4j_admin,
    create_vision,
    workspace_folder_graph,
)
from graphme.server.exports import Exports
from graphme.server.jobs import (
    BUILDING,
    CANCELLED,
    PROTECTED,
    STARTING,
    UNDRAWN,
    Busy,
    CorpusBuild,
    GraphStarting,
    Kept,
    Session,
    Undrawn,
)
from graphme.server.workspaces import Workspaces

__all__ = [
    # Read by the layers through this module, so a test stands in for each in one place.
    "GraphMe", "content_hash", "copy_graph", "create_graph_builder", "create_neo4j_admin",
    "create_vision", "embedded_in", "evict", "online_only", "vault", "workspace_folder_graph",
    # Imported from here by the routes and the tests.
    "CANCELLED", "PROTECTED", "SPACE_WAIT", "UNDRAWN", "VLM_CALLS_AT_ONCE", "Busy",
    "CorpusBuild", "Demo", "GraphStarting", "Undrawn", "kept_from",
]

VLM_CALLS_AT_ONCE = 24
"""Model calls in flight across every document, each reading and each settling call counted
on its own. Lower it when the endpoint's rate limit turns calls away; a call turned away is
retried, so too high costs time rather than pages."""
READERS = 16
"""Documents read side by side; their model calls share VLM_CALLS_AT_ONCE besides. Each
document asks as many pages at once as the slots hold, and the slots decide who goes: a share
fixed when a document started left a long one, still reading after the rest had finished,
asking four pages at a time while the other slots stood empty. What that costs is threads
waiting for a slot, which do nothing while they wait."""

def _stamp(path: str | Path) -> tuple[int, int] | None:
    """A file's size and modification time, or None when it is not there."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_size, status.st_mtime_ns


PREVIEWS_KEPT = 24
PREVIEW_AROUND = 5
"""How many pages before and after one drawn for a reader are drawn behind it, in one go: a
reader turns the pages of what they opened, and a slide deck's page drawn alone starts a
browser and reads the whole file each time."""
EMBED_WAIT_SECONDS = 30.0
"""How long a workspace switch waits for the embedding the open workspace started."""
PREVIEW_HOLD_SECONDS = 300.0
"""How long a cloud drive's file downloaded to draw a page is kept after it was last looked at:
the next page of the same document is drawn from it, not downloaded again."""
GIVE_BACK_TICK = 30.0
"""How often what was looked at is checked for being given back."""


def preview_limit(settings: Settings) -> int:
    """The bytes the pages kept for readers may take on disk."""
    return settings.preview_cache_mb * 1_000_000


def _keep_page(folder: Path, name: str, png: bytes, limit: int) -> None:
    """`png` kept as `name`, then the oldest pages let go until what is kept fits `limit`."""
    if limit <= 0:
        return
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(handle, "wb") as out:
        out.write(png)
    os.replace(temporary, folder / name)
    kept = []
    for path in folder.glob("*.png"):
        with contextlib.suppress(OSError):
            found = path.stat()
            kept.append((found.st_mtime, found.st_size, path))
    total = sum(size for _, size, _ in kept)
    for _, size, path in sorted(kept):
        if total <= limit:
            break
        path.unlink(missing_ok=True)
        total -= size


def _give_back_for_good(paths: list[Path]) -> None:
    """`paths` given back to their cloud drive now, and again at each of `SETTLE_SECONDS` if
    an indexer had the drive download them since."""
    if not paths:
        return
    evict(paths)
    threading.Thread(target=_settle, args=(
        lambda: evict([p for p in paths if not online_only(p)]),), daemon=True).start()


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
    # A figure's part is shown by the figure's crop: the figure holds it, and keeps the only one.
    for node in records["nodes"]:
        figure = node["properties"].get("meta_part_of_figure")
        if node["id"] not in crops and figure in crops:
            crops[node["id"]] = crops[figure]
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
_log = logging.getLogger(__name__)
SPACE_WAIT = 2.0
READ_TIMEOUT_SECONDS = 4 * 60 * 60
"""How long a build waits for one document's reading before counting it as failed."""
SETTLE_SECONDS: tuple[float, ...] = (30.0, 120.0, 600.0)
"""When, after a build, the files it gave back are looked at again: macOS's Spotlight indexes a
file the build downloaded -- reading it after it was given back, so the drive downloads it
again -- and leaves it alone once indexed."""
SWEEP_GRACE = 600.0
"""Seconds an asset just written is kept by a sweep, named or not: a reading under way writes
its crops before the graph names them."""
ROOT_PREFIX = "hierarchy-demo-"
"""What a working folder made for a moment (a test) is called in the system's temporary
folder."""


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
"""How an OLE container begins. An Office file of the zip formats read here is in one only when
Office sealed it -- a password or rights protection wraps the zip inside it."""


def _sealed(session: Session) -> bool:
    if session.format == DocumentFormat.PDF:
        return False
    try:
        with open(session.source, "rb") as file:
            return file.read(len(OLE_MAGIC)) == OLE_MAGIC
    except OSError:
        return False


def _settle(again: Callable[[], None]) -> None:
    """`again` once at each of `SETTLE_SECONDS` after a build."""
    waited = 0.0
    for at in SETTLE_SECONDS:
        time.sleep(at - waited)
        waited = at
        try:
            again()
        except Exception:  # noqa: BLE001 -- the file stays here; the next look tries again
            traceback.print_exc(file=sys.stderr)


def _in_workspace(settings: Settings, spaces: Workspaces, workspace: str) -> Settings:
    """`settings` for `workspace`: its scope in the one Neo4j, its folder for crops and mail."""
    return settings.model_copy(
        update={"workspace": workspace, "local_graph": spaces.graph(workspace)})


class Demo(Exports):
    """The server's state, built in layers, each a module that sees the layers under it:
    `demo_base` (the state itself, the files held), `space_jobs` (the graph space and its
    workspaces), `reading`, `previews`, `corpus_builds`, `cloud_sources`, `exports`; here, new
    settings and a mailbox's reading."""

    def reconfigure(self, settings: Settings) -> None:
        """New keys or model, taken up from the next reading and the next question. The graph
        space is kept: what it is reached by is not among what changes. The settings a save
        loads name no home, so where the app's own Neo4j and data live is carried over -- without
        it the next workspace opened looked for a Neo4j at the default address."""
        searched_with = self.settings.embedding_provider
        settings = settings.model_copy(update={
            "local_graph": self.settings.local_graph, "workspace": self.settings.workspace,
            "local_neo4j": self.settings.local_neo4j, "data_dir": self.settings.data_dir})
        # The graph being opened -- a first start's move takes minutes -- is said, not waited.
        if not self.opening_space.acquire(timeout=SPACE_WAIT):
            raise GraphStarting(STARTING)
        try:
            self.settings = settings
            self.graph.settings = settings
            self.graph.forget_rag()
        finally:
            self.opening_space.release()
        with self.siding:
            self._sides.clear()  # made again with the new keys when next asked
        if settings.embedding_provider != searched_with:
            self.search_models.chosen_changed()

    def build_mail(self, account: str, since: date | None) -> CorpusBuild:
        """`account`'s mailbox read into its workspace, opened first: the store brought level
        with the mailbox from `since` on (newest first) as the build's first step, then the
        messages built like any documents -- progress, cancel and the attachments they carry
        as for a folder. Reading `since` again after a cancel picks up where it stopped."""
        accounts = self.mail_accounts()
        item = accounts.get(account)
        # Not the agent's take on opening: this read builds what the store holds, and a
        # build the agent started first would refuse it.
        self.switch_workspace(item["workspace"], take_mail=False)
        self._wait_for_embed()  # opening it embeds what it holds, behind: a second or two

        def find(build: CorpusBuild) -> list[str]:
            # Inside the job: a read refused because a build runs must not have changed the
            # account that build is reading.
            accounts.start_reading(account, since)
            try:
                result = accounts.sync(account, self.build_stop)
            except MailRefused as error:
                raise ValueError(str(error)) from None  # the build's error, as a folder's
            # What the mailbox no longer holds leaves the graph as the agent's removals do:
            # held, and deleted by its next pass (not inside this build).
            self.mail_sync.hold(account, [], result.removed)
            return [self.open_path(str(path))
                    for path in sorted(accounts.messages(account).glob("*.eml"))]

        return self.build_corpus([], item["address"], find=find, empty_done=True)

    def disconnect_mail(self, account: str, workspace: str) -> None:
        """`account`'s password and messages forgotten, its workspace deleted (`workspace` is
        "delete") or kept as an ordinary one. Busy while that workspace builds: the job reads
        the store being removed. A workspace gone already is not a reason to keep the account."""
        accounts = self.mail_accounts()
        spaces = self.workspace_registry()
        home = accounts.get(account)["workspace"]
        if spaces.active == home and self.build.state in BUILDING:
            raise Busy("the account is being read -- cancel that first")
        if workspace == "delete":
            with contextlib.suppress(LookupError):
                self.delete_workspace(home)  # forgets the account too
        with contextlib.suppress(LookupError):
            accounts.forget(account, keep_messages=workspace == "keep")
        if workspace == "keep":
            with contextlib.suppress(LookupError):
                spaces.release(home)








































































"""What the server keeps of each reading, build and export, the states they pass through, and
what it says when one fails. `state.Demo` runs them."""
from __future__ import annotations

import sys
import threading
import traceback
from collections.abc import Iterable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.storage.assets import LocalAssetStore
from graphme.application.corpus_build import (
    BuildStep,
)
from graphme.domain.enums import DocumentFormat
from graphme.domain.models import ExtractedDocument

Placed = dict[str, tuple[int, tuple[int, int, int, int]]]
"""Where each node is drawn: its page and its pixel box there (`placed_units`)."""


@dataclass(frozen=True)
class Kept:
    """A document as the graph space holds it, with where its build drew each node."""

    records: dict[str, Any]
    page_of: dict[str, int]
    boxes: dict[str, tuple[int, list[int]]]
    crops: dict[str, str]


class GraphStarting(RuntimeError):
    """The graph DB is being fetched or started and is not up yet: ask again shortly."""


class Busy(Exception):
    """Refused because the work it would disturb is under way: a build, an export, a
    reading. Said as 409."""


class Undrawn(Exception):
    """A document's pages could not be drawn again. Said as 503: the file is there, the
    renderer is what failed."""


IN_LOG = "(details in the server log)"
GONE = "this document's source file cannot be found"
STARTING = "the graph DB is starting -- this carries on by itself once it is ready."
UNREAD = f"could not be read -- the file may be damaged or hold nothing readable {IN_LOG}"
PAGE_UNREAD = f"this page could not be read {IN_LOG}"
UNDRAWN = f"the pages could not be drawn {IN_LOG}"


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
    preparing: threading.Lock = field(default_factory=threading.Lock)
    kept: Kept | None = None
    """What the graph space holds of a document built earlier -- where each node is drawn,
    its pages, its graph -- when it was opened from there: shown with no extraction, no
    binding and no drawing but of the page looked at."""
    origin: str = ""
    """Where the original is, as the graph space records it (`SourceStore`): a path on this
    machine, or a cloud drive's address."""
    stamp: tuple[int, int] | None = None
    """The file's size and time when this reading began: another means the file changed."""
    """Its pages are drawn by one request; another asking meanwhile waits for those pages
    instead of drawing its own. Another document's pages are not held up by them."""

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
CANCELLED = "not read: the build was cancelled"
"""Why a document a stopped build passed over was not read -- the end of its reason."""


class Cancelled(Exception):
    """A document's reading stopped between steps because its build was cancelled."""

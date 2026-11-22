"""What the server's API takes and gives, as types: the contract `/docs` shows and the web
app is generated from. A field added here is a field a client can read; one the server sends
without saying so here is dropped on the way out."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# Documents --------------------------------------------------------------------------------


class LibraryItem(_Model):
    name: str
    format: str
    in_space: bool
    """In the graph space, or only held (or being read) by the server."""
    document_id: str | None = None
    """Its id in the graph space."""
    file_id: str | None = None
    """The id of the server's file for it, which draws its pages."""
    state: str
    modified: float | None = None
    """When its file was last changed, in seconds."""
    web_url: str | None = None
    """Where its original opens in a browser: a cloud drive's page; none for a local file."""


class Library(_Model):
    items: list[LibraryItem]
    space_ok: bool
    """False when the graph DB cannot be reached; the server's own files are still listed."""


class Uploaded(_Model):
    id: str
    """The file's id."""
    state: str
    """`in_space` when its content is in the graph already, so a build passes it over; else
    where its reading stands (`idle` until a job reads it)."""


class Derived(_Model):
    node: str
    to: str


class PageInfo(_Model):
    index: int
    name: str | None = None
    width: int
    height: int
    derived: list[Derived] | None = None
    seconds: float | None = None
    pending: bool
    error: str | None = None


class ReadingProgress(_Model):
    stage: Literal["figures", "paragraphs", "outline", "captions", "references"]
    """The stage under way."""
    done: int
    total: int
    """That stage's questions answered and planned."""
    percent: int
    """Every planned question answered so far, over all of them (0-100)."""


class Pages(_Model):
    source: str
    format: str
    state: str
    error: str | None = None
    pending: bool
    sheets: list[PageInfo]
    progress: ReadingProgress | None = None
    """How far the reading is, once it has begun."""
    elapsed: float | None = None
    """Seconds its reading has taken: still counting while it reads; None never read here."""


class PagesOf(_Model):
    file_id: str
    """The file that draws the pages of a document in the space."""


# The graph space ----------------------------------------------------------------------------


class BuildRequest(_Model):
    folder: str | None = None
    """A folder on the server's machine, or --"""
    paths: list[str] = Field(default_factory=list)
    """-- folders and files on the server's machine (what was checked in Box Drive), or --"""
    files: list[str] = Field(default_factory=list)
    """-- files uploaded (`POST /v1/files`)."""
    embed: bool = True
    """Its passages embedded once it is in, when an embedding key is set."""


class ReadRequest(_Model):
    files: list[str]
    """Files uploaded, read the way a build reads them and not put in the graph space."""


class BuildState(_Model):
    id: str
    kind: Literal["build", "read", "embed"]
    state: str
    """idle | reading | linking | embedding | done | cancelled | error"""
    done: int
    total: int
    source: str
    failed: list[str]
    documents: int
    known: int
    carried: int = 0
    """Files found inside the documents given, read as documents of their own and counted in
    `total` -- known only once the build has looked, when `total` is first said."""
    links: int
    """Citation links this build made. Similarity links come with embedding and are counted
    only in the map."""
    skipped: int = 0
    embedded: int
    pages_read: int = 0
    """Pages the model read for this build's documents."""
    pages_reused: int = 0
    """Pages that had not changed since an earlier reading, taken as they were read: an
    edited document is read again only where it changed."""
    percent: int | None = None
    """How far the reading is, over every document's questions (0-100); None for a job that
    reads nothing."""
    looked: int = 0
    """Documents found so far while the folders picked are looked through."""
    error: str | None = None
    elapsed: float | None = None
    """Seconds the job has taken: still counting while it runs."""


class MapDocument(_Model):
    id: str
    label: str
    title: str | None = None
    mentions: int = 0
    modified_at: float | None = None
    content_hash: str | None = None


class MapLink(_Model):
    from_: str = Field(alias="from")
    to: str
    count: int
    matched_by: list[str]


class SimilarLink(_Model):
    from_: str = Field(alias="from")
    to: str
    score: float


class CorpusMap(_Model):
    documents: list[MapDocument]
    total: int
    """Documents in the space; the map draws at most MAP_LIMIT of them."""
    links: list[MapLink]
    similar: list[SimilarLink]


class GraphNode(_Model):
    id: str
    labels: list[str]
    properties: dict[str, Any]


class GraphRelationship(_Model):
    id: str
    type: str
    start: str
    end: str
    properties: dict[str, Any]


class ChunkSpan(_Model):
    id: str
    members: list[str]
    importance: float | None = None


class DocumentGraph(_Model):
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]
    chunks: list[ChunkSpan] = Field(default_factory=list)
    pages: dict[str, int] = Field(default_factory=dict)
    """The page each node is drawn on (a file the server read, only)."""
    crops: list[str] = Field(default_factory=list)
    """The nodes with a stored crop (a file the server read, only)."""


class Placed(_Model):
    page: int
    box: tuple[int, int, int, int]
    """left, top, right, bottom, in the rendered page's pixels."""


class Boxes(_Model):
    boxes: dict[str, Placed]
    """Where each node is drawn, by node id."""


class Confirm(_Model):
    confirm: bool = False


class Reset(_Model):
    deleted: int


class Deleted(_Model):
    deleted: str


class Opened(_Model):
    opened: str
    """The file's name, as it was opened."""


# Questions ----------------------------------------------------------------------------------


class Turn(_Model):
    """An earlier question of the conversation and the answer it was given."""
    question: str
    answer: str


class Question(_Model):
    question: str = ""
    documents: int | None = None
    """How many documents to find; the server's setting when not given."""
    top_k: int | None = Field(default=None, deprecated=True)
    """The old name of `documents`, read as it."""
    workspace: str | None = None
    """The workspace asked, by id; the open one when not given."""
    workspaces: list[str] | None = None
    """Several workspaces asked at once, by id: each searched in its own graph, the results
    fused by rank. None or empty: `workspace` alone."""
    history: list[Turn] = Field(default_factory=list)
    """The conversation's earlier turns, oldest first: a follow-up is searched as the question
    it stands for. Only the latest few are read, each answer's opening."""
    only: list[str] | None = None
    """Document ids the question is about: only their passages, and the documents linked to
    them inside the list, are searched. None: every document. Ids a workspace does not hold
    find nothing there."""
    exclude: list[str] | None = None
    """Document ids left out of this question: none of their passages is found and none is
    brought as a linked document."""

    @model_validator(mode="before")
    @classmethod
    def _old_name(cls, data: Any) -> Any:
        # Read here, from the request body: reading the deprecated field itself warns.
        if isinstance(data, dict) and data.get("documents") is None and "top_k" in data:
            return {**data, "documents": data["top_k"]}
        return data


class ScopeRequest(_Model):
    paths: list[str]
    """Files and folders on this computer; a folder stands for the supported files under it."""


class Elsewhere(_Model):
    path: str
    workspace: str


class Scope(_Model):
    document_ids: list[str]
    """The documents of the files `workspace` holds, by content."""
    missing: list[str]
    """The files no workspace holds."""
    elsewhere: list[Elsewhere] = []
    """The files another workspace than `workspace` holds; they are never built."""
    workspace: str | None = None
    """The workspace holding most of the files (the open one wins a tie); None when none holds
    any. Without a workspace registry, the one open graph is named by the settings' id."""
    build_into: str | None = None
    """Where a missing file may be built: `workspace`, else the open one; never a mail
    workspace -- then the open one if it is not mail, else None (nothing is buildable)."""


class LinkedPassage(_Model):
    id: str
    score: float
    members: list[str]
    text: str


class LinkedDocument(_Model):
    document_id: str
    file: str
    hop: int
    via: str
    direction: str
    """How `via` is linked to it: cites | cited_by | similar."""
    passages: list[LinkedPassage]
    modified: float | None = None


class LinkedFrom(_Model):
    via: str
    hop: int
    direction: str
    """How `via` is linked to it: cites | cited_by | similar."""


class Source(_Model):
    id: str
    score: float
    document_id: str
    file: str
    members: list[str]
    context: str
    linked: list[LinkedDocument] = Field(default_factory=list)
    modified: float | None = None
    origin: str | None = None
    """The mail it came from, or the mail that carried its document."""
    workspace: str | None = None
    """The workspace it was found in."""
    promoted_from: LinkedFrom | None = None
    """Set when this is a linked document ranked among the found ones by its own passages:
    where it was reached from, and its hop."""


class Found(_Model):
    sources: list[Source]
    queries: list[str] = Field(default_factory=list)
    """What was searched: the question, or each thing it asks as a query of its own."""
    text_only: bool = False
    """Searched by words alone: the workspace's passages were embedded with another search
    model, and it is not the one open."""


class Claim(_Model):
    text: str
    """As the answer writes it."""
    supported: bool
    correction: str | None = None
    """When unsupported: the claim as the passages say it; none when they say nothing of it."""
    files: list[str] = Field(default_factory=list)


class Answer(_Model):
    answer: str
    sources: list[Source]
    queries: list[str] = Field(default_factory=list)
    """What was searched: the question, or each thing it asks as a query of its own."""
    text_only: bool = False
    claims: list[Claim] = Field(default_factory=list)
    checked: bool = False
    """Whether the claims were checked against the passages; false when off or failed."""


class Checked(_Model):
    """An answer's claims once checked: the `checked` event of a streamed question."""
    claims: list[Claim] = Field(default_factory=list)
    checked: bool = False


class StreamError(_Model):
    """What a streamed question says when it fails after its stages began."""
    message: str


# Threads: conversations kept on this computer -------------------------------------------------


class ThreadTurn(_Model):
    question: str
    scope: list[str] | None = None
    """The documents the turn was asked about; None for every document."""
    found: Found | None = None
    answer: Answer | None = None
    stamp: str | None = None
    """The fingerprint of the graph the turn was asked of, as the client gave it."""
    asked_at: float
    error: str | None = None
    """Why the turn has no answer, when it failed."""


class Thread(_Model):
    id: str
    title: str = ""
    """The first question, unless renamed."""
    scope: list[str] | None = None
    """The documents every turn is asked about; None for every document."""
    workspace: str
    turns: list[ThreadTurn] = Field(default_factory=list)
    created_at: float
    updated_at: float


class ThreadSummary(_Model):
    id: str
    title: str
    updated_at: float
    turns: int


class NewThread(_Model):
    scope: list[str] | None = None
    workspace: str | None = None
    """The workspace the thread asks, by id; the open one when not given."""


class ThreadPatch(_Model):
    title: str | None = None
    scope: list[str] | None = None
    """Given, even as null, it replaces the scope; left out, the scope stays."""


class TurnRequest(_Model):
    question: str
    documents: int | None = None
    """How many documents to find; the server's setting when not given."""
    stamp: str | None = None
    """The fingerprint of the graph the client sees, kept with the turn."""
    exclude: list[str] | None = None
    """Document ids left out of this turn alone; the thread's scope is not changed."""


# Export -------------------------------------------------------------------------------------


class ExportRequest(_Model):
    docs: list[str] | None = None
    """None is every document; an empty list is refused."""


class AuraUpload(ExportRequest):
    uri: str = ""
    user: str = ""
    password: str = ""
    confirm: bool = False


class ExportJob(_Model):
    id: str
    kind: Literal["dump", "aura-upload"]
    state: str
    """idle | dumping | uploading | done | error"""
    message: str
    documents: int | None = None
    finished_at: float | None = None
    dump_ready: bool = False
    """Its `.dump` can be fetched (`GET /v1/jobs/{id}/file`): a dump, done, and the latest."""


Job = Annotated[BuildState | ExportJob, Field(discriminator="kind")]


class SettingField(_Model):
    env: str
    """The variable it is kept under in `.env`."""
    secret: bool
    set: bool
    value: str | None = None
    """The value, for a setting that is not a secret."""
    hint: str | None = None
    """A secret's last four characters, when it is set -- the secret itself is never sent."""
    choices: dict[str, str] | None = None
    """For a provider: what it can be, each with the variable its key is kept under."""


class BoxItem(_Model):
    type: Literal["folder", "file"]
    id: str
    name: str


class Box(_Model):
    configured: bool
    """Whether the user's Box app is set (GRAPHME_BOX_CLIENT_ID and _SECRET)."""
    connected: bool
    """Whether a Box account is signed in."""
    picked: list[BoxItem]
    """The folders and files picked for the graph."""


class BoxConnect(_Model):
    url: str
    """Where the browser signs in to Box; opened already when the server could."""


class BoxPlace(_Model):
    id: str
    name: str


class BoxListed(_Model):
    id: str
    type: Literal["folder", "file"]
    name: str
    size: int | None = None
    modified: float | None = None
    """When it was last changed, in seconds since 1970."""
    readable: bool = True
    """A folder, or a document read here: what can be picked."""
    picked: bool = False
    """Picked itself -- a file inside a picked folder is read with it, but not marked."""


class BoxFolder(_Model):
    id: str
    name: str
    path: list[BoxPlace]
    """The folders above it, the top first."""
    items: list[BoxListed]
    """Its folders, then its documents; nothing else it holds."""


DriveKind = Literal["box", "google", "onedrive", "dropbox", "icloud", "synology"]
"""A drive whose desktop client is read: Box Drive, Google Drive for desktop, OneDrive (with
the SharePoint libraries it syncs)."""


class DesktopDrive(_Model):
    found: bool
    """Whether the drive's desktop client shows it on this machine."""
    root: str | None = None
    """Where it is."""
    connected: bool = False
    """Whether the user said to read it: asked once, then kept for every start."""


class DriveUsage(_Model):
    bytes: int
    """What the drive's downloaded files take on this machine."""
    files: int


class BoxPick(_Model):
    items: list[BoxItem]
    """Everything picked: what was picked before is replaced."""


class ServerSettings(_Model):
    endpoint: str
    """Who answers the chat model: the GRAPHME_PROVIDER the server runs with."""
    fields: list[SettingField]
    problems: list[str]
    """What keeps the server from building or answering, as the startup check says it."""
    samples: bool
    """The server writes sample documents to try it on (`POST /v1/samples`, the demo's)."""


class Health(_Model):
    status: str
    version: str
    home: str


class SettingsChange(_Model):
    values: dict[str, str]
    """Only what changes; an empty value leaves that setting as it is."""


class Fetching(_Model):
    done: int
    total: int
    """Bytes fetched so far, and of how many (0 until the size is known)."""


class SearchModel(_Model):
    name: str
    """`general` or `korean` -- what GRAPHME_EMBEDDING_PROVIDER names it."""
    model: str
    size: int
    """The file's size in bytes."""
    installed: bool
    chosen: bool
    """The one questions are searched with."""
    fetching: Fetching | None = None
    error: str | None = None
    """Why its last install failed."""



class Workspace(_Model):
    id: str
    name: str
    mcp: bool = False
    """Whether an MCP client may read it: off until switched on in the app."""
    kind: Literal["documents", "mail"] = "documents"
    """A mail workspace reads its account's mailbox only: no folder, drive or upload."""
    account: str | None = None
    """The mail account read into a mail workspace."""


class Workspaces(_Model):
    available: bool
    """Whether this server keeps workspaces: the app's own graph DB does, another one not."""
    active: str | None = None
    """The workspace open now."""
    mcp_enabled: bool = True
    """The one switch for all MCP clients; each workspace's own is kept while it is off."""
    mcp_changes: bool = False
    """Whether MCP clients may change things -- delete, reset, settings -- not only read."""
    items: list[Workspace] = Field(default_factory=list)
    unmoved: dict[str, str] = Field(default_factory=dict)
    """Workspaces whose graph from before could not be moved from their folder into the one
    graph DB at this start, and why: still in their folder, tried again at the next start."""


class WorkspaceName(_Model):
    name: str


class WorkspaceChange(_Model):
    name: str | None = None
    mcp: bool | None = None


class McpSwitch(_Model):
    enabled: bool | None = None
    changes: bool | None = None
    """Whether a client may change things (delete, reset, settings), not only read."""


class MailConnect(_Model):
    host: str
    port: int = 993
    address: str
    password: str
    """Passed to the mail server and kept in the credential store; never answered."""


class MailCountState(_Model):
    messages: int
    attachments: int
    attachment_bytes: int | None = None
    """None where the service tells no size without fetching each message (Gmail)."""
    oldest: date | None = None


class MailAccount(_Model):
    id: str
    kind: Literal["imap", "gmail", "outlook"] = "imap"
    host: str
    port: int
    address: str
    workspace: str
    """The workspace the account is read into: its own."""
    state: Literal["counting", "counted", "reading", "current", "error"]
    counting: int | None = None
    """While `counting`: how many messages are counted so far."""
    count: MailCountState | None = None
    read_since: date | None = None
    """Mail older than this is not read yet; None, all of it is."""
    last_checked: str | None = None
    error: str | None = None
    """What the mail server said when it refused."""
    waiting: int = 0
    """Messages read in but not yet in the graph: its workspace is closed, or a build runs.
    They go in when the workspace is opened."""


class MailRead(_Model):
    since: date | None = None


# Automatic builds -----------------------------------------------------------------------

WatchKind = Literal["folder", "drive", "mail"]
WatchMode = Literal["manual", "on_change", "daily"]


class WatchMissing(_Model):
    path: str
    document_id: str | None = None
    """Its document in the graph, deleted with `DELETE /v1/documents/{document_id}`; None
    when the graph cannot be asked now."""


class WatchFolder(_Model):
    path: str
    kind: Literal["folder", "drive"]


class WatchTarget(_Model):
    kind: WatchKind
    id: str
    workspace: str
    mode: WatchMode
    """`manual` builds only when asked; `on_change` once nothing has changed for a few
    minutes; `daily` at `at`."""
    at: str | None = None
    """A daily build's time, HH:MM on this machine's clock."""
    path: str | None = None
    """A folder target's folder."""
    name: str
    """What the target is called: the folder's name, the mail address."""
    changed: int
    """Documents added or changed since the last build (a mail account: mail held)."""
    missing: list[WatchMissing] = []
    """Files built before and gone from the folder since: still in the graph."""
    next_build: str | None = None
    """When it builds next (ISO, this machine's time); None when nothing is due."""
    building: bool = False
    error: str | None = None
    """Why its folder (or a mail account's mailbox) cannot be read now; nothing is built."""


class WatchChoice(_Model):
    mode: WatchMode
    at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    path: str | None = None
    """A new folder or drive target's folder."""
    workspace: str | None = None
    """The workspace a new folder target builds into; the open one when None."""

    @model_validator(mode="after")
    def _daily_has_a_time(self) -> WatchChoice:
        if self.mode == "daily" and not self.at:
            raise ValueError("a daily build needs its time, HH:MM")
        return self

"""What the server's API takes and gives, as types: the contract `/docs` shows and the web
app is generated from. A field added here is a field a client can read; one the server sends
without saying so here is dropped on the way out."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


# Questions ----------------------------------------------------------------------------------


class Question(_Model):
    question: str = ""
    top_k: int | None = None


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


class Source(_Model):
    id: str
    score: float
    document_id: str
    file: str
    members: list[str]
    context: str
    linked: list[LinkedDocument] = Field(default_factory=list)
    modified: float | None = None


class Found(_Model):
    sources: list[Source]


class Answer(_Model):
    answer: str
    sources: list[Source]


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


DriveKind = Literal["box", "google", "onedrive"]
"""A drive whose desktop client is read: Box Drive, Google Drive for desktop, OneDrive (with
the SharePoint libraries it syncs)."""


class DesktopDrive(_Model):
    found: bool
    """Whether the drive's desktop client shows it on this machine."""
    root: str | None = None
    """Where it is."""
    connected: bool = False
    """Whether the user said to read it: asked once, then kept for every start."""


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


class Workspaces(_Model):
    available: bool
    """Whether this server keeps workspaces: the app's own graph DB does, another one not."""
    active: str | None = None
    """The workspace open now."""
    items: list[Workspace] = Field(default_factory=list)


class WorkspaceName(_Model):
    name: str

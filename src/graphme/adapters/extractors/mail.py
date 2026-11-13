"""A mail message read as a document: its headers are metadata, its body is text units.

Nothing is drawn and no model reads the body. The HTML part is taken when there is one, since
it keeps tables; else the plain part. A message has no pages, so no unit carries a box.
"""

from __future__ import annotations

import email
import itertools
import re
from dataclasses import dataclass, field
from datetime import UTC
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.policy import default
from html.parser import HTMLParser
from pathlib import Path

from graphme.domain.enums import DocumentFormat, EdgeType, EvidenceKind, NodeType
from graphme.domain.ids import canonical_locator, sha256_bytes, sha256_file, stable_id
from graphme.domain.models import (
    DocumentRecord,
    EvidenceRecord,
    ExtractedDocument,
    GraphEdge,
    GraphNode,
    JsonValue,
    SourceLocator,
    native_edge,
)

FOLDER_HEADER = "X-GMM-Folder"
"""The header a store writes to say which mailbox folder a message came from."""



def without_folder_stamp(raw: bytes) -> bytes:
    """The message with any `X-GMM-Folder` header (and its folded lines) removed. The store
    stamps a message itself, so one a server sent cannot name the folder for it."""
    ends = [at for at in (raw.find(b"\r\n\r\n"), raw.find(b"\n\n")) if at >= 0]
    cut = min(ends) if ends else len(raw)
    kept: list[bytes] = []
    dropping = False
    for line in raw[:cut].splitlines(keepends=True):
        if line[:1] not in (b" ", b"\t"):
            dropping = line.lower().startswith(f"{FOLDER_HEADER}:".lower().encode())
        if not dropping:
            kept.append(line)
    return b"".join(kept) + raw[cut:]


def message_key(raw: bytes) -> str:
    """What names a message: its Message-ID, else a digest of its bytes. The folder stamp a
    store prepends is left out of the digest, so one message kept from two folders, or read
    before and after being stamped, has one key. The store names its file by this key."""
    raw = without_folder_stamp(raw)
    head = BytesHeaderParser(policy=default).parsebytes(raw)
    return str(head["Message-ID"] or "").strip() or "sha256:" + sha256_bytes(raw)[:32]


_BLOCKS = frozenset({
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
    "section", "article", "header", "footer", "hr", "dl", "dt", "dd", "center",
})
_SKIPPED = frozenset({"style", "script", "head", "title"})
_HIDDEN = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_SOURCE_BREAK = re.compile(r"[\r\n]+")
_VOID = frozenset({"br", "img", "hr", "input", "meta", "link", "wbr", "col", "area", "base",
                   "source"})
"""Elements with no end tag: hiding one hides nothing after it."""
_SPACE = re.compile(r"[ \t\r\n\f ]+")


_Unit = tuple[str, str, bool]


@dataclass
class _Table:
    """A table being read: its rows of cells, each cell the units read inside it."""

    rows: list[list[list[_Unit]]] = field(default_factory=list)
    row: list[list[_Unit]] | None = None
    cell: list[_Unit] | None = None
    nested: bool = False

    def laid_out(self) -> bool:
        """A table that shapes the page rather than holding values: it holds another table,
        or no row has two cells with words in them. Its units are read as if it were not
        there; a table of values keeps its rows and cells."""
        return self.nested or all(sum(1 for c in row if c) <= 1 for row in self.rows)


class _HtmlBody(HTMLParser):
    """Paragraphs and tables, in order: `(kind, text, quoted)` with kind `text` or `table`.
    Quoted is true inside a `blockquote` or Gmail's `gmail_quote`, and from Outlook's
    `divRplyFwdMsg` to the end."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[_Unit] = []
        self._opened: list[bool] = []
        self._quoting = 0
        self._after = False
        self._line: list[str] = []
        self._skipping = 0
        self._tables: list[_Table] = []
        self._hidden: str | None = None
        """The element hidden by its style the parser is inside, and how many of it are open."""
        self._hidden_open = 0
        self._pre = 0

    def _put(self, unit: _Unit) -> None:
        """A unit read: into the cell being read, else the body."""
        table = self._tables[-1] if self._tables else None
        if table is None:
            self.units.append(unit)
        elif table.cell is not None:
            table.cell.append(unit)
        else:  # words between cells: a row of their own
            table.rows.append([[unit]])

    def _flush(self) -> None:
        text = "".join(self._line)
        self._line = []
        lines = [_SPACE.sub(" ", each).strip() for each in text.split("\n")]
        text = "\n".join(each for each in lines if each)
        if text:
            self._put(("text", text, self._quoting > 0 or self._after))

    def _end_cell(self, table: _Table) -> None:
        self._flush()
        if table.cell is not None:
            if table.row is None:
                table.row = []
            table.row.append(table.cell)
            table.cell = None

    def _end_row(self, table: _Table) -> None:
        self._end_cell(table)
        if table.row is not None:
            table.rows.append(table.row)
            table.row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hidden:  # only the hidden element's own end matters
            self._hidden_open += tag == self._hidden
            return
        if tag in _SKIPPED:
            self._skipping += 1
        elif self._skipping:
            return
        elif _HIDDEN.search(dict(attrs).get("style") or "") and tag not in _VOID:
            self._hidden, self._hidden_open = tag, 1  # not shown to the reader: not read
        elif tag == "table":
            self._flush()
            if self._tables:
                self._tables[-1].nested = True
            self._tables.append(_Table())
        elif self._tables and tag == "tr":
            self._end_row(self._tables[-1])
            self._tables[-1].row = []
        elif self._tables and tag in ("td", "th"):
            self._end_cell(self._tables[-1])
            self._tables[-1].cell = []
        elif tag == "br":
            self._line.append("\n")
        elif tag in _BLOCKS:
            self._flush()
            self._pre += tag == "pre"
            if tag in ("div", "blockquote"):
                named = dict(attrs)
                if named.get("id") == "divRplyFwdMsg":
                    self._after = True
                opens = tag == "blockquote" or "gmail_quote" in (named.get("class") or "").split()
                self._opened.append(opens)
                self._quoting += opens

    def handle_endtag(self, tag: str) -> None:
        if self._hidden:
            if tag == self._hidden:
                self._hidden_open -= 1
                if not self._hidden_open:
                    self._hidden = None
            return
        if tag in _SKIPPED:
            self._skipping = max(0, self._skipping - 1)
        elif self._skipping:
            return
        elif tag == "table" and self._tables:
            self._end_row(self._tables[-1])
            table = self._tables.pop()
            if table.laid_out():
                for row in table.rows:
                    for cell in row:
                        for unit in cell:
                            self._put(unit)
                return
            # A cell's paragraphs are words of one value; a table inside a cell, its rows.
            rows = [[" ".join(text.replace("\n", " ") for _, text, _ in cell) for cell in row]
                    for row in table.rows]
            rows = [row for row in rows if any(row)]
            if rows:
                self._put(("table", "\n".join(" | ".join(row) for row in rows),
                           self._quoting > 0 or self._after))
        elif self._tables and tag in ("td", "th"):
            self._end_cell(self._tables[-1])
        elif self._tables and tag == "tr":
            self._end_row(self._tables[-1])
        elif tag in _BLOCKS:
            self._flush()
            self._pre = max(0, self._pre - (tag == "pre"))
            if tag in ("div", "blockquote") and self._opened:
                self._quoting -= self._opened.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self._skipping and not self._hidden:
            # A newline in the source is a space to the reader; only <br> (and <pre>) break a
            # line -- `_flush` splits on the "\n" those leave.
            self._line.append(data if self._pre else _SOURCE_BREAK.sub(" ", data))

    def close(self) -> None:
        super().close()
        while self._tables:  # a table never closed is read as far as it went
            self.handle_endtag("table")
        self._flush()


def _text_of(part: EmailMessage) -> str:
    try:
        return str(part.get_content())
    except (LookupError, ValueError):  # a charset Python does not know
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""


_QUOTE_FROM = re.compile(r"^(On .+ wrote:|20\d\d.+ 작성:)$")
_ORIGINAL = re.compile(r"^-{3,}\s*Original Message\s*-{3,}$", re.IGNORECASE)
_SENT = re.compile(r"^(Sent|보낸 날짜|보낸 사람):", re.IGNORECASE)


def _plain_units(text: str) -> list[tuple[str, str, bool]]:
    """Plain text as units: `>` lines, a line saying who wrote what and all after it, and
    Outlook's "-----Original Message-----" (or a "From:" line with "Sent:" just below) and all
    after it are quoted history; blank lines keep the side they are on."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sides: list[bool] = []
    after = False
    for at, line in enumerate(lines):
        stripped = line.strip()
        following = next((each.strip() for each in lines[at + 1:at + 2]), "")
        wrapped = ((stripped.startswith("On ") and following.endswith("wrote:"))
                   or (re.match(r"20\d\d", stripped) and following.endswith("작성:")))
        if not after and (
                wrapped or _QUOTE_FROM.match(stripped) or _ORIGINAL.match(stripped)
                or (stripped.startswith("From:")
                    and any(_SENT.match(each.strip()) for each in lines[at + 1:at + 5]))):
            after = True
        sides.append(after or stripped.startswith(">")
                     or (not stripped and bool(sides) and sides[-1]))
    units: list[tuple[str, str, bool]] = []
    for quoted, run in itertools.groupby(zip(lines, sides, strict=True), key=lambda pair: pair[1]):
        cleaned = [re.sub(r"^(\s*>)+ ?", "", line) if quoted else line for line, _ in run]
        pieces = ["\n".join(cleaned)] if quoted else re.split(r"\n\s*\n", "\n".join(cleaned))
        units.extend(("text", each.strip(), quoted) for each in pieces if each.strip())
    return units


def mail_body(message: EmailMessage) -> list[tuple[str, str, bool]]:
    """The body as `(kind, text, quoted)` units: `text` paragraphs and `table` rows, the
    quoted history of earlier messages marked. Quoted paragraphs side by side are one unit."""
    part = message.get_body(preferencelist=("html", "plain"))
    if not isinstance(part, EmailMessage):
        return []
    text = _text_of(part)
    if part.get_content_subtype() != "html":
        return _plain_units(text)
    parser = _HtmlBody()
    parser.feed(text)
    parser.close()
    units: list[tuple[str, str, bool]] = []
    for kind, each, quoted in parser.units:
        if quoted and kind == "text" and units and units[-1][0] == "text" and units[-1][2]:
            units[-1] = ("text", f"{units[-1][1]}\n{each}", True)
        else:
            units.append((kind, each, quoted))
    return units


def _addresses(message: EmailMessage, name: str) -> list[JsonValue]:
    found: list[JsonValue] = []
    for header in message.get_all(name, []):
        addresses = getattr(header, "addresses", None)
        if addresses is None:
            found.append(str(header))
        else:
            found.extend(str(each) for each in addresses)
    return found


def _headers(message: EmailMessage, raw: bytes) -> dict[str, JsonValue]:
    found: dict[str, JsonValue] = {}
    found["mail_message_id"] = message_key(raw)
    if message["In-Reply-To"]:
        found["mail_in_reply_to"] = str(message["In-Reply-To"]).strip()
    found["mail_references"] = [*str(message["References"] or "").split()]
    if message["From"]:
        found["mail_from"] = str(message["From"])
    found["mail_to"] = _addresses(message, "To")
    found["mail_cc"] = _addresses(message, "Cc")
    try:
        sent = message["Date"].datetime if message["Date"] else None
    except (AttributeError, ValueError):  # a date nobody can read
        sent = None
    if sent is not None:
        found["mail_date"] = sent.isoformat()
        # ISO strings with different offsets do not sort by time: this does.
        found["mail_sent_at"] = (sent if sent.tzinfo else sent.replace(tzinfo=UTC)).timestamp()
    if message["Subject"] is not None:
        found["mail_subject"] = str(message["Subject"])
    if message[FOLDER_HEADER]:
        found["mail_folder"] = str(message[FOLDER_HEADER]).strip()
    return found


class MailExtractor:
    """Read an `.eml`: headers as the document's metadata, the body as its units."""

    format = DocumentFormat.EML

    def extract(self, source: Path) -> ExtractedDocument:
        raw = source.read_bytes()
        message = email.message_from_bytes(raw, policy=default)
        assert isinstance(message, EmailMessage)
        content_hash = sha256_file(source)
        document_id = stable_id(content_hash, canonical_locator("document", {"file": source.name}))
        metadata = _headers(message, raw)
        root = SourceLocator(file_name=source.name, label=source.name)
        nodes: list[GraphNode] = [GraphNode(
            id=document_id, document_id=document_id, type=NodeType.DOCUMENT, label=source.name,
            locator=root, content_hash=content_hash, metadata=dict(metadata))]
        evidence: list[EvidenceRecord] = [EvidenceRecord(
            id=stable_id(content_hash, "evidence:document"), document_id=document_id,
            node_id=document_id, kind=EvidenceKind.SOURCE_METADATA, locator=root,
            text=f"document {source.name}")]
        edges: list[GraphEdge] = []
        quote = {"mail_quoted": True, "mail_quote_of": str(metadata.get("mail_in_reply_to", ""))}
        for index, (kind, text, quoted) in enumerate(mail_body(message), start=1):
            label = f"body {kind} {index}"
            locator = SourceLocator(file_name=source.name, label=label,
                                    metadata={"body_index": index})
            node_id = stable_id(content_hash, canonical_locator(kind, {"body": index}))
            nodes.append(GraphNode(
                id=node_id, document_id=document_id,
                type=NodeType.TABLE if kind == "table" else NodeType.TEXT_CHUNK,
                label=text, locator=locator, text=text,
                content_hash=sha256_bytes(text.encode()),
                metadata={"body_index": index, **(quote if quoted else {})}))
            evidence.append(EvidenceRecord(
                id=stable_id(content_hash, f"evidence:{kind}:{index}"), document_id=document_id,
                node_id=node_id, kind=EvidenceKind.SOURCE_TEXT, locator=locator, text=text))
            edges.append(native_edge(
                content_hash, document_id, document_id, node_id, EdgeType.CONTAINS))
        extracted = ExtractedDocument(
            document=DocumentRecord(
                id=document_id, content_hash=content_hash, file_name=source.name,
                format=DocumentFormat.EML, source_path=source, size_bytes=len(raw),
                metadata={"native_extractor": "email", **metadata}),
            nodes=tuple(nodes), edges=tuple(edges), evidence=tuple(evidence))
        return extracted.validate_canonical_semantic_graph()


def is_mail(document: GraphNode) -> bool:
    """A message: it carries a Message-ID (or the digest standing in for one)."""
    return "mail_message_id" in document.metadata

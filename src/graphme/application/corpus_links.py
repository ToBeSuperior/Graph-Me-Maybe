"""Documents in one graph: a document another names is that document, once both are here.

A page that cites another document leaves an `external_document` node: empty, because the
named document was not here to read. Once it is -- both indexed in the same store -- the
mention and the document are one thing, and `SAME_AS` says so: from the mention to the
named document's root, so a reader can walk from the citing sentence into what it cites.

This runs over the whole corpus, not inside one document's build. A document cited before
it was indexed is linked the moment it is, and nothing depends on which arrived first.

A document answers to its file name, the name without its extension, a document number
in its file name, and the title its outline holds it under. It answers as well, word for word,
to the title its file states (`file_titles.stated_titles`) and to a title's part after its
colon -- "Quick Guide to the Leak Test Bench", cited without the series it is part of
("FLUXTRAN Handbook 3: ...") -- unless more than one document has that part.
A file carried inside another
(`embedded_documents.py`) is named with a digest of its bytes, and answers to that digest:
the same attachment in two documents is one document, whatever each carrier called it.
A mention is linked to every other document that answers to it -- two versions of one
file, both -- and each link says by which rule (`matched_by`). Whether a mention names a
document is that pair's own question, so a document added, read again or gone changes only
its own links.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from graphme.application.final_edges import final_edges
from graphme.domain.enums import EdgeType, NodeType
from graphme.domain.models import ExtractedDocument, GraphEdge
from graphme.domain.text_match import folded, letters, normalized

MIN_NAME_CHARS = 4
"""Shorter than this, a name -- "7", "A1" -- answers to too much to mean one document."""

MIN_TITLE_CHARS = 20
"""A title is matched only when it is long enough to be one: "Introduction" is not. Counted
by `title_size`, so a Korean title is held to as much as an English one says, not as many
characters."""

TITLE_PREFIX_LETTERS = 30
"""A title's first line, when the outline held the paper under only that line: this many
letters and digits from the start of a title are that title, and fewer could be any."""

TITLE_OVERLAP = 0.8
"""How much of the longer of a name and a title the shorter has to be, when one holds the
other: a citation trims a subtitle or adds the venue, and is still that title."""

_EMBEDDED = re.compile(r"\.emb([0-9a-f]{8})\.[A-Za-z]+$")


def embedded_name(name: str, content: bytes) -> str:
    """What a file carried inside a document is read under: its own name, and a digest of its
    bytes that both its citations and its document answer to."""
    own = PurePosixPath(name)
    digest = hashlib.sha256(content).hexdigest()[:8]
    return f"{own.stem}.emb{digest}{own.suffix.lower()}"


def embedded_digest(name: str) -> str | None:
    found = _EMBEDDED.search(name)
    return found.group(1) if found else None


_WORDS = re.compile(r"[\s_()\[\],;:]+")
MIN_NUMBER_CHARS = 6
"""A document number: a word of letters and digits joined by hyphens, with at least one of
each -- "K310215-0042", "XQ-2031-0007" -- long enough not to be a version."""

MIN_NUMBER_DIGITS = 4
"""As many digits as a document number has at least: "COVID-19", "SHA-256" and "GPT-4o" name
a thing, not a document."""

_DATE = re.compile(r"(?:19|20)\d\d(?:-\d{1,2}){1,2}|\d{1,2}-\d{1,2}-(?:19|20)\d\d"
                   r"|(?:19|20)\d\d-(?:19|20)\d\d")
"""A date or a span of years: it heads many file names and names none of them."""

_DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2212", "-"))
"""The dashes a page or a model writes where a document number has a hyphen."""

_UNNAMED = frozenset({
    "document", "doc", "file", "report", "untitled", "new", "draft", "final", "copy",
    "attachment", "appendix", "annex", "no", "pdf", "docx", "pptx", "ppt", "xlsx", "xls", "hwp",
    "hwpx", "txt",
    "문서", "파일", "제목", "없음", "새", "최종", "사본", "첨부", "붙임", "별첨", "별지",
    "제", "호", "서식", "양식",
})
"""The words a file is called when nobody named it, or named it by its place in a bundle:
"Document1", "untitled", "report.pdf", "붙임 1", "별지 제1호 서식"."""

_POINTING = frozenset({
    "the", "this", "that", "these", "those", "attached", "enclosed", "above", "below",
    "following", "said", "present", "본", "이", "해당", "상기", "아래", "위", "다음",
})
"""Words pointing at what lies at hand: "the attached checklist", "본 문서"."""

_KINDS = frozenset({
    "checklist", "list", "form", "guide", "manual", "table", "sheet", "template", "memo",
    "letter", "presentation", "slide", "book", "workbook", "minutes", "notes", "자료", "보고서",
    "결과보고서", "목록", "체크리스트", "안내서", "지침", "서류", "회의록", "발표자료",
})
"""What kind of document a pointing phrase points at."""

_WORDS_OF = re.compile(r"[^\W\d_]+")


def generic_name(text: str) -> bool:
    """Whether a name says nothing that tells one document from another: a file nobody named
    ("Document1", "붙임 1"), or what lies at hand pointed at ("the attached checklist") --
    with letter labels ("Appendix A") and numbers that are not a document's
    (`document_numbers`) besides. A word with a particle ("문서의") or a plural is that word."""
    if document_numbers(text):
        return False
    words = [word for word in _WORDS_OF.findall(folded(text))
             if len(word) > 1 or not word.isascii()]

    def among(word: str, known: frozenset[str]) -> bool:
        return word in known or len(word) > 1 and word[:-1] in known

    if all(among(word, _UNNAMED) for word in words):
        return True
    return any(among(word, _POINTING) for word in words) and all(
        among(word, _UNNAMED | _POINTING | _KINDS) for word in words)


def title_size(text: str) -> int:
    """How much a title says: a Hangul syllable as much as two Latin letters."""
    return len(text) + sum(1 for character in text if "가" <= character <= "힣")


def _file_names(text: str) -> set[str]:
    """A file name as written, and with its underscores as the spaces a page writes."""
    return {folded(text), folded(text.replace("_", " "))}


@dataclass(frozen=True, slots=True)
class DocumentIdentity:
    """What a document answers to: its file and, where its outline holds it under one, its
    title. Read off a document's own graph, or off the graph space it was put in.

    The names and numbers it answers to are worked out once, when it is made, and looked up
    by every mention in the corpus (`DocumentLookup`)."""

    document_id: str
    file_name: str
    title: str | None = None
    stated: tuple[str, ...] = ()
    """Titles the file states of itself, matched only word for word."""
    names: frozenset[str] = field(init=False, compare=False)
    numbers: frozenset[str] = field(init=False, compare=False)
    title_letters: str | None = field(init=False, compare=False)
    digest: str | None = field(init=False, compare=False)
    exact: frozenset[str] = field(init=False, compare=False)
    """The stated titles, as their letters and digits."""
    subtitles: frozenset[str] = field(init=False, compare=False)
    """What follows the colon in a title, as its letters and digits."""

    def __post_init__(self) -> None:
        exact = {letters(title) for title in self.stated}
        object.__setattr__(self, "exact", frozenset(
            t for t in exact if title_size(t) >= MIN_TITLE_CHARS))
        object.__setattr__(self, "subtitles", frozenset(
            part for title in (self.title, *self.stated) if title
            for part in (letters(each) for each in normalized(title).split(":")[1:])
            if title_size(part) >= MIN_TITLE_CHARS
        ))
        object.__setattr__(self, "digest", embedded_digest(self.file_name))
        stem = Path(self.file_name).stem
        object.__setattr__(self, "names", frozenset(
            () if generic_name(stem) else _file_names(self.file_name) | _file_names(stem)))
        object.__setattr__(self, "numbers", document_numbers(stem))
        object.__setattr__(
            self, "title_letters", letters(self.title) if self.title is not None else None
        )


@dataclass(frozen=True, slots=True)
class Mention:
    """An `external_document` node: another document, named on a page of this one."""

    node_id: str
    document_id: str
    name: str


def relinked(
    mentions: Sequence[Mention], identities: Sequence[DocumentIdentity], arrived: set[str]
) -> tuple[list[str], tuple[GraphEdge, ...]]:
    """What documents just put in the space change: the mentions to judge again -- every
    mention in them, and every mention already here that one of them answers to -- and the
    links those mentions now make, judged against every document. The rest stand as they
    were, so a space of thousands is not linked again for each document added."""
    newcomers = DocumentLookup([each for each in identities if each.document_id in arrived])
    # Whether a subtitle names a document is the whole space's question -- one more document
    # with it, or one fewer, changes it -- so a mention written as one is judged again always.
    subtitles = {part for each in identities for part in each.subtitles}
    again = [mention for mention in mentions
             if mention.document_id in arrived or newcomers.answers(mention)
             or letters(mention.name) in subtitles]
    return [mention.node_id for mention in again], link_mentions(again, identities)


def link_mentions(
    mentions: Sequence[Mention], identities: Sequence[DocumentIdentity]
) -> tuple[GraphEdge, ...]:
    """Join every mention to every other document that answers to its name."""
    lookup = DocumentLookup(identities)
    links: list[GraphEdge] = []
    for mention in sorted(mentions, key=lambda each: (each.document_id, each.node_id)):
        links += [
            GraphEdge(
                id=f"same-as:{mention.node_id}->{target}",
                document_id=mention.document_id,
                source_id=mention.node_id,
                target_id=target,
                type=EdgeType.SAME_AS,
                metadata={"matched_by": matched_by},
            )
            for target, matched_by in lookup.named(mention)
        ]
    return tuple(links)


LINK_RULE = (f"pairwise/embedded,file_name,number,stated,subtitle,title/name{MIN_NAME_CHARS}"
             f"/title{MIN_TITLE_CHARS}"
             f"-{TITLE_PREFIX_LETTERS}-{TITLE_OVERLAP}"
             f"/nfkc/hangul2/digits{MIN_NUMBER_DIGITS}-nodates/generic/numberbound")
"""Names how a mention is judged against a document; stored on each document once its
mentions and the mentions naming it have been judged, so a document a build put but never
linked -- the build stopped -- or judged by another rule is judged again."""

TITLE_KEY_LETTERS = MIN_TITLE_CHARS
"""A name held inside a title starts with letters the title holds somewhere, and a title held
inside a name starts with letters the name holds: this many of them -- as many as a name must
have to be held against a title at all -- find the titles worth comparing."""


class DocumentLookup:
    """What each document answers to, looked up rather than held against every document: a
    name finds the documents with that name, a number those with that number, and a title
    the few that share a run of its letters -- then each is judged as it always was."""

    def __init__(self, identities: Sequence[DocumentIdentity]) -> None:
        unique = {identity.document_id: identity for identity in identities}
        self._by_name: dict[str, set[str]] = {}
        self._by_number: dict[str, set[str]] = {}
        self._by_digest: dict[str, set[str]] = {}
        self._by_stated: dict[str, set[str]] = {}
        self._by_subtitle: dict[str, set[str]] = {}
        subtitled: dict[str, set[str]] = {}
        self._by_inner: dict[str, set[str]] = {}
        self._by_start: dict[str, set[str]] = {}
        self._short: set[str] = set()
        self._titles: dict[str, str] = {}
        for document_id, identity in unique.items():
            for name in identity.names:
                self._by_name.setdefault(name, set()).add(document_id)
            if identity.digest is not None:
                self._by_digest.setdefault(identity.digest, set()).add(document_id)
            for stated in identity.exact:
                self._by_stated.setdefault(stated, set()).add(document_id)
            for part in identity.subtitles:
                subtitled.setdefault(part, set()).add(document_id)
            for number in identity.numbers:
                self._by_number.setdefault(number, set()).add(document_id)
            title = identity.title_letters
            if title is None:
                continue
            self._titles[document_id] = title
            if len(title) < TITLE_KEY_LETTERS:
                self._short.add(document_id)
                continue
            self._by_start.setdefault(title[:TITLE_KEY_LETTERS], set()).add(document_id)
            for key in _runs(title):
                self._by_inner.setdefault(key, set()).add(document_id)
        # A subtitle more than one document has ("Overview") names none of them.
        for part, holders in subtitled.items():
            if len(holders) == 1:
                self._by_subtitle[part] = holders

    def named(self, mention: Mention) -> list[tuple[str, str]]:
        """Every document other than the mention's own this name means, each with the first
        rule it answers by, in id order."""
        found: dict[str, str] = {}
        for matched_by, answering in self._answering(mention):
            for document_id in answering:
                found.setdefault(document_id, matched_by)
        return sorted(found.items())

    def answers(self, mention: Mention) -> bool:
        """Whether any document here answers to the mention, by any rule -- one that may
        change what it names."""
        return any(found for _, found in self._answering(mention))

    def _answering(self, mention: Mention) -> Iterator[tuple[str, set[str]]]:
        """Rule by rule, the documents other than the mention's own that answer to it."""
        wanted = folded(mention.name)
        if len(wanted) < MIN_NAME_CHARS or generic_name(mention.name):
            return
        own = {mention.document_id}
        written = letters(mention.name)
        digest = embedded_digest(mention.name)
        yield "embedded", (self._by_digest.get(digest, set()) if digest else set()) - own
        yield "file_name", set().union(
            *(self._by_name.get(name, set()) for name in _file_names(mention.name))) - own
        numbers = document_numbers(mention.name)
        yield "number", set().union(
            *(self._by_number.get(number, set()) for number in numbers)) - own
        yield "stated", self._by_stated.get(written, set()) - own
        yield "subtitle", self._by_subtitle.get(written, set()) - own
        yield "title", {
            document_id for document_id in self._title_candidates(written)
            if _same_title(written, self._titles[document_id])} - own

    def _title_candidates(self, written: str) -> set[str]:
        if title_size(written) < MIN_TITLE_CHARS:
            return set()
        held_in_title = self._by_inner.get(written[:TITLE_KEY_LETTERS], set())
        holding_title = set().union(
            *(self._by_start.get(key, set()) for key in _runs(written)))
        # A title shorter than the key can only hold a name at most 1/TITLE_OVERLAP its size.
        short = self._short if len(written) * TITLE_OVERLAP < TITLE_KEY_LETTERS else set()
        return held_in_title | holding_title | short


def _runs(text: str) -> set[str]:
    return {text[i : i + TITLE_KEY_LETTERS] for i in range(len(text) - TITLE_KEY_LETTERS + 1)}


def identity_of(extracted: ExtractedDocument) -> DocumentIdentity:
    return DocumentIdentity(
        document_id=extracted.document.id,
        file_name=extracted.document.file_name,
        title=_title(extracted),
    )


def _title(extracted: ExtractedDocument) -> str | None:
    """What the outline holds the document under: the top unit holding the most of it."""
    root = extracted.document.id
    parent = {edge.target_id: edge.source_id for edge in final_edges(extracted)}
    held: dict[str, int] = {}
    for node_id in parent:
        seen: set[str] = set()
        current = parent.get(node_id)
        while current is not None and current != root and current not in seen:
            seen.add(current)
            held[current] = held.get(current, 0) + 1
            current = parent.get(current)
    nodes = {node.id: node for node in extracted.nodes}
    tops = [
        node_id
        for node_id, holds in held.items()
        if holds and parent.get(node_id) == root and node_id in nodes
        and nodes[node_id].type is NodeType.TEXT_CHUNK
    ]
    if not tops:
        return None
    top = max(tops, key=lambda node_id: held[node_id])
    title = folded(nodes[top].text or nodes[top].label)
    return title if title_size(title) >= MIN_TITLE_CHARS else None


def _same_title(name: str, title: str) -> bool:
    """Whether a name is this title, both given as their letters and digits only: a
    reference list loses a hyphen at a line break ("handannotated" for "hand-annotated") and
    adds or drops punctuation."""
    if title_size(name) < MIN_TITLE_CHARS:
        return False
    shorter, longer = sorted((name, title), key=len)
    if title_size(shorter) >= TITLE_PREFIX_LETTERS and _held_at(shorter, longer, 0) and not (
        shorter is name and _hangul(longer[len(shorter):])
    ):
        return True  # a title wrapped, and only its first line was taken as the title
    if len(shorter) < TITLE_OVERLAP * len(longer):
        return False
    start = longer.find(shorter)
    while start != -1:
        # A Korean title ends in what kind of document it is ("기준" and "기준 해설서",
        # "법률" and "법률 시행령"): a word more in Hangul is another document.
        if _held_at(shorter, longer, start) and not (
            _hangul(longer[:start]) or _hangul(longer[start + len(shorter):])
        ):
            return True
        start = longer.find(shorter, start + 1)
    return False


def _hangul(text: str) -> bool:
    return any("가" <= character <= "힣" for character in text)


def _held_at(shorter: str, longer: str, start: int) -> bool:
    """Whether `longer` holds `shorter` at `start`, and not as the first digits of a longer
    number: "part1" is not held in "part10", nor "version1" in "version11"."""
    end = start + len(shorter)
    return longer.startswith(shorter, start) and not (
        start and longer[start - 1].isdigit() and shorter[0].isdigit()
    ) and not (end < len(longer) and longer[end].isdigit() and shorter[-1].isdigit())


def document_numbers(text: str) -> frozenset[str]:
    """The document numbers written in `text`: words of letters and digits joined by hyphens,
    long enough not to be a version (MIN_NUMBER_CHARS), with a number's digits
    (MIN_NUMBER_DIGITS), and not a date. Any dash a page writes is a hyphen."""
    words = (word.strip(".") for word in _WORDS.split(normalized(text).translate(_DASHES)))
    return frozenset(
        word.casefold()
        for word in words
        if len(word) >= MIN_NUMBER_CHARS
        and "-" in word
        and sum(character.isdigit() for character in word) >= MIN_NUMBER_DIGITS
        and all(character.isalnum() or character == "-" for character in word)
        and not _DATE.fullmatch(word)
    )

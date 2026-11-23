"""Which linked documents a found passage brings along, and where they rank.

A found passage's document reaches others by its links (`GraphRag._linked`); this keeps those
something says are about the question (`relevant_links`), the nearest few at each hop, once a
document and family (`_once`), and ranks a linked document among the found ones when it is
nearer the question than one of them (`promoted`).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import neo4j
from neo4j_graphrag.types import RetrieverResultItem

from graphme.application.versions import Member, VersionOf, family_key, versions_of

LINKED_DOCUMENTS = 4
"""Linked documents a found passage brings along at each hop, the ones nearest the question:
a much-cited paper is linked to dozens, and all of them is a context nobody answers from."""


@dataclass(frozen=True, slots=True)
class LinkedPassage:
    """A passage of a linked document, brought along because it is near the question."""

    id: str
    score: float
    """Its similarity to the question."""
    members: list[str]
    text: str


@dataclass(frozen=True, slots=True)
class LinkedDocument:
    """A document reached from a found passage's document by following the links between
    documents: hop 2 is linked to that document, hop 3 to a hop-2 document."""

    document_id: str
    file: str
    hop: int
    via: str
    """The document it was reached from."""
    direction: str
    """How `via` is linked to it: "cites" it, is "cited_by" it, or is "similar" to it."""
    passages: list[LinkedPassage]
    modified: float | None = None
    """When its file was last changed (`Document.modified_at`)."""


@dataclass(frozen=True, slots=True)
class LinkedFrom:
    """Where a linked document ranked among the found ones (`promoted`) was reached from."""

    via: str
    hop: int
    direction: str


@dataclass(frozen=True, slots=True)
class GraphSource:
    """A passage an answer was given, and the documents the graph reached from its own:
    the passage's document is hop 1, the documents linked to it hop 2, theirs hop 3."""

    id: str
    score: float
    document_id: str
    file: str
    members: list[str]
    """The units it covers, in reading order."""
    context: str
    """The passage exactly as the model saw it, with what the graph brought along."""
    linked: list[LinkedDocument] = field(default_factory=list)
    modified: float | None = None
    """When its document's file was last changed (`Document.modified_at`)."""
    origin: str | None = None
    """The mail it came from, or the mail that carried its document (`mail_origin`)."""
    workspace: str | None = None
    """The workspace it was found in, once said (`find_across`, `ask_across`, the server)."""
    promoted_from: LinkedFrom | None = None
    """Set when this is a linked document's passage ranked among the found ones (`promoted`)."""
    versions: list[VersionOf] = field(default_factory=list)
    """The other documents of its family, folded under it (`_once`)."""


def relevant_links(linked: Sequence[dict[str, Any]], floor: float) -> list[dict[str, Any]]:
    """The linked documents something says are about the question: each keeps only its
    passages whose cosine reaches `floor` (`vector.similarity.cosine` gives (1 + cos) / 2, as
    the index does), and one with none left is not brought along -- a link says two documents
    belong together, not that the other one answers this question."""
    kept = []
    for doc in linked:
        near = [p for p in doc["passages"] if 2 * p["score"] - 1 >= floor]
        if near:
            kept.append({**doc, "passages": near})
    return kept


def promoted(sources: Sequence[GraphSource], discount: float, named: set[str],
             closeness: Mapping[str, float]) -> list[GraphSource]:
    """`sources` with each linked document ranked by its own passages: its best passage's
    closeness to the question, times `discount` for each hop past the first -- none when the
    question names it -- set against each found passage's `closeness`, the same measure of
    the same question vector (a found passage's fused score ranks it within its search and
    says nothing of how near it is). One nearer than a found passage stands in its place as
    that passage of its own, with the place's score, saying how it was reached
    (`promoted_from`); it leaves the document it was reached from, taking along the hop-3
    documents reached through it. A found document the question names keeps its place ahead
    (`named_first`), and a passage with no closeness -- a search by words alone -- is passed
    by none. Only the order changes: every document shown before is shown, and no other."""
    near = dict(closeness)
    rows = [(source, list(source.linked)) for source in sources]
    reached = sorted(
        ((best.score * (1.0 if doc.document_id in named else discount ** (doc.hop - 1)),
          doc, best)
         for _, linked in rows for doc in linked if doc.passages
         for best in [max(doc.passages, key=lambda p: p.score)]),
        key=lambda each: -each[0])
    for score, doc, best in reached:
        at = next((i for i, (found, _) in enumerate(rows)
                   if found.document_id not in named and found.id in near
                   and score > near[found.id]), None)
        if at is None:
            continue
        parent, linked = next(row for row in rows if doc in row[1])
        carried = [d for d in linked if d.hop == 3 and d.via == doc.document_id]
        for each in [doc, *carried]:
            linked.remove(each)
        near[best.id] = score  # one ranked after it stays after it
        rows.insert(at, (GraphSource(
            id=best.id, score=rows[at][0].score, document_id=doc.document_id, file=doc.file,
            members=best.members, context=best.text, linked=carried, modified=doc.modified,
            workspace=parent.workspace,
            promoted_from=LinkedFrom(via=doc.via, hop=doc.hop, direction=doc.direction)),
            carried))
    return [replace(source, linked=linked) for source, linked in rows]


def _marked(records: list[neo4j.Record], named: set[str]) -> list[neo4j.Record]:
    """`records` with each found and linked document saying whether the question names it
    (`named`), for `promoted` to read once the passages are items."""
    if not named:
        return records
    return [neo4j.Record({**dict(record),  # type: ignore[no-untyped-call]
                          "named": record["document_id"] in named,
                          "linked": [{**doc, "named": doc["document_id"] in named}
                                     for doc in record["linked"]]})
            for record in records]


def _closeness_in(items: Sequence[RetrieverResultItem]) -> dict[str, float]:
    """Each found passage's closeness to the question (`_linked`), by its id; none when the
    question was searched by its words alone."""
    return {m["id"]: m["closeness"] for item in items for m in [item.metadata or {}]
            if m.get("closeness") is not None}


def _named_in(items: Sequence[RetrieverResultItem]) -> set[str]:
    """The documents among `items`, found or linked, that the question names (`_marked`)."""
    named: set[str] = set()
    for item in items:
        found = item.metadata or {}
        named |= {found["document_id"]} if found.get("named") else set()
        named |= {doc["document_id"] for doc in found.get("linked", []) if doc.get("named")}
    return named


def _once(records: list[neo4j.Record], *, each_folder: bool = False,
          family: Mapping[str, Sequence[Member]] | None = None) -> list[neo4j.Record]:
    """Each found passage with the documents linked to its own that it brings along: at each
    hop the LINKED_DOCUMENTS whose passages come nearest the question, hop 2 before hop 3, a
    hop-3 document only from a hop-2 one it kept. A document is shown once, at the first hop
    that reaches it -- one a question found (hop 1) is never brought along again, and a linked
    one goes with the best-ranked passage that reaches it.

    An answer takes LINKED_DOCUMENTS at hop 3 in all, so what the model reads stays short;
    `each_folder` -- the Q folder page, where every hop-2 document is a folder -- takes that
    many under each hop-2 document instead.

    A document stands for its `family` (`versions.by_document`): a version of one shown is not
    brought along, and each found document carries its family's others not shown (`versions`)."""
    family = family or {}

    def best(doc: dict[str, Any]) -> float:
        return max((p["score"] for p in doc["passages"]), default=-1.0)

    def key(doc: str) -> str:
        return family_key(family, doc)

    shown = {key(record["document_id"]) for record in records}
    kept: list[list[dict[str, Any]]] = [[] for _ in records]
    for hop in (2, 3):
        for record, mine in zip(records, kept, strict=True):
            reachable = {doc["document_id"] for doc in mine}
            candidates = sorted(
                (doc for doc in record["linked"]
                 if doc["hop"] == hop and (hop == 2 or doc["via"] in reachable)),
                key=best, reverse=True)
            taken: dict[str | None, int] = {}
            for doc in candidates:
                under = doc["via"] if each_folder and hop == 3 else None
                if taken.get(under, 0) < LINKED_DOCUMENTS and key(doc["document_id"]) not in shown:
                    shown.add(key(doc["document_id"]))
                    mine.append(doc)
                    taken[under] = taken.get(under, 0) + 1
    seen = {record["document_id"] for record in records} | {
        doc["document_id"] for mine in kept for doc in mine}

    def versions(doc: str) -> list[VersionOf]:
        return versions_of(family[doc], seen) if doc in family else []

    return [neo4j.Record({**dict(record),  # type: ignore[no-untyped-call]
                          "versions": versions(record["document_id"]),
                          "linked": mine})
            for record, mine in zip(records, kept, strict=True)]

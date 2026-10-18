"""A folder of documents into one graph, in three steps that each wait for the one before.

1. Each document is read on its own -- extraction, the outline, captions, the documents
   its pages name -- exactly as a single document is.
2. It is put in the graph space as soon as it is read.
3. Only then are the mentions joined, as far as this build changes them
   (`corpus_links.relinked`): the mentions in its documents, and those already in the
   space one of its documents answers to, are each judged against every document in the
   space, and a `SAME_AS` goes from the mention to the one document it names.

The third step reads the space, not the folder, so a document put there by an earlier
build is matched as much as one from this folder, and a mention left empty before is
joined the moment the document it names arrives.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from neo4j.exceptions import ServiceUnavailable, SessionExpired

from graphme.application.corpus_links import LINK_RULE, DocumentIdentity, Mention, relinked
from graphme.domain.ids import sha256_file
from graphme.domain.models import ExtractedDocument, GraphEdge

_log = logging.getLogger(__name__)


def content_hash(path: Path) -> str | None:
    """The SHA-256 of a file -- what the space knows a document's content by; None when it
    cannot be read here."""
    try:
        return sha256_file(path)
    except OSError:
        return None


class BuildStep(StrEnum):
    """The three steps of a build, in the order they run."""

    READ = "read"
    PUT = "put"
    LINK = "link"


StepListener = Callable[[BuildStep, int, int], None]
"""Hears the step a build is in and how far along it is: (step, done, total)."""


class GraphSpace(Protocol):
    """The one graph every document of a corpus is put in."""

    def put_document(self, extracted: ExtractedDocument) -> None: ...

    def delete_documents(self, document_ids: Sequence[str]) -> None: ...

    def identities(self) -> tuple[DocumentIdentity, ...]: ...

    def mentions(self) -> tuple[Mention, ...]: ...

    def contents(self, hashes: Sequence[str]) -> set[str]:
        """Which of these file contents (SHA-256) the space already holds."""
        ...

    def unlinked(self, rule: str) -> set[str]:
        """The documents whose mentions were not judged by `rule` -- put since, or never."""
        ...

    def mark_linked(self, documents: Sequence[str], rule: str) -> None: ...

    def put_links(self, links: Sequence[GraphEdge], *, of: Sequence[str]) -> None:
        """`links` in place of the SAME_AS drawn before from the mentions `of`."""
        ...


@dataclass(frozen=True, slots=True)
class BuildReport:
    processed: int
    failed: tuple[tuple[Path, str], ...]
    links: int
    """The links this build judged -- its documents' mentions and the mentions naming them --
    not every link in the space."""
    known: int = 0
    """Files passed over: their content was in the space already."""


def build_graph(
    documents: Sequence[Path],
    *,
    process: Callable[[Path], ExtractedDocument],
    space: GraphSpace,
    on_step: StepListener | None = None,
    again: bool = False,
) -> BuildReport:
    """Read every document, put them all in `space`, then join the mentions there.

    `on_step` hears the step and how far it is ("read", done, total), so a caller can say
    which of the three a long build is in.
    """
    told: StepListener = on_step or (lambda step, done, total: None)
    # Reading costs a model call a page: a file already in the space is passed over, unless
    # it is to be read `again` (the reading itself changed).
    hashes = {path: None if again else content_hash(path) for path in documents}
    held = set() if again else space.contents(sorted(h for h in hashes.values() if h))
    known = [path for path in documents if hashes[path] in held]
    documents = [path for path in documents if hashes[path] not in held]
    put: set[str] = set()
    failed: list[tuple[Path, str]] = []
    for index, path in enumerate(documents, start=1):
        told(BuildStep.READ, index - 1, len(documents))
        # One document that cannot be read is one document, not the folder -- whatever stopped
        # it: a file that does not parse, a provider that turned it away after its retries.
        try:
            extracted = process(path)
        except Exception as error:  # noqa: BLE001 -- recorded and reported, never swallowed
            _log.warning("%s: not read -- %s", path, error)
            failed.append((path, str(error)))
            continue
        # Put as soon as read: a folder is never held whole, and a build stopped half way
        # keeps every document it read.
        told(BuildStep.PUT, index - 1, len(documents))
        try:
            space.put_document(extracted)
        except (ServiceUnavailable, SessionExpired):
            raise  # the space is gone, not this document: reading on would only spend model calls
        except Exception as error:  # noqa: BLE001 -- the write is one transaction: all or none
            _log.warning("%s: not put -- %s", path, error)
            failed.append((path, str(error)))
            continue
        put.add(extracted.document.id)
    told(BuildStep.PUT, len(documents), len(documents))

    told(BuildStep.LINK, 0, 1)
    identities = space.identities()
    # And any document an earlier build put but stopped before linking.
    arrived = put | space.unlinked(LINK_RULE)
    rejudged, links = relinked(space.mentions(), identities, arrived)
    space.put_links(links, of=rejudged)
    space.mark_linked(sorted(arrived), LINK_RULE)
    told(BuildStep.LINK, 1, 1)
    return BuildReport(processed=len(put), failed=tuple(failed), links=len(links),
                       known=len(known))


def forget_documents(space: GraphSpace, document_ids: Sequence[str]) -> None:
    """Documents gone from the space, and what that changes in the rest judged again now: a
    subtitle two documents shared names the one left (`corpus_links.relinked`)."""
    space.delete_documents(document_ids)
    rejudged, links = relinked(space.mentions(), space.identities(), set())
    space.put_links(links, of=rejudged)

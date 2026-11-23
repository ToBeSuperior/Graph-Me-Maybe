"""An answer's claims checked against the passages it cites, by one more model call.

The check reads the passages carrying the files the answer cites -- a passage carries its own
file and the linked documents written under it -- and keeps only claims the answer holds. A
claim whose sentence cites only files the answer was never given is not supported, whatever
the check said. An answer citing no file has nothing to check against and is not sent.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from neo4j_graphrag.types import RetrieverResultItem

from graphme.adapters.storage.answer_prompt import CHECK_SYSTEM, _Claims
from graphme.adapters.strict_schema import strict_response_schema
from graphme.application.embedded_documents import shown_name
from graphme.domain.text_match import folded

_log = logging.getLogger(__name__)

_CITED = re.compile(r"\[([^\]]+)\]")
_TRAILING_CITES = re.compile(r"([.!?。])((?:\s*\[[^\]]+\])+)")
"""Citations written after a sentence's full stop ("... 24 months. [a.pdf]"): moved before
it, so they stay with the sentence they cite for."""


@dataclass(frozen=True, slots=True)
class Claim:
    """One thing an answer says, checked against the passages it cites."""
    text: str
    """As the answer writes it."""
    supported: bool
    correction: str | None
    """When unsupported: the claim as the passages say it; None when they say nothing of it."""
    files: list[str]


def _sentences(answer: str) -> list[str]:
    return re.split(r"(?<=[.!?。])\s+", _TRAILING_CITES.sub(r"\2\1", answer))


def _cites_unread(answer: str, claim: str, known: set[str]) -> bool:
    """Whether the sentence of `answer` holding `claim` cites only files the answer was not
    given: a passage elsewhere may say the same, but the source it names was never read."""
    for sentence in _sentences(answer):
        if folded(claim) in folded(sentence):
            named = set(_CITED.findall(sentence))
            return bool(named) and not named & known
    return False


def carried_files(item: RetrieverResultItem) -> set[str]:
    """The files a passage carries as the answer read them (`shown_name`): its own and the
    linked documents written under it, each a file the answer may cite."""
    found = item.metadata or {}
    files = [found.get("file", ""), *(doc["file"] for doc in found.get("linked", []))]
    return {shown_name(unicodedata.normalize("NFC", str(file))) for file in files}


def checked(answer: str, items: Sequence[RetrieverResultItem],
            complete: Callable[..., str]) -> list[Claim] | None:
    """What each claim of `answer` comes to against the passages carrying the files it cites;
    None when it cites none or the check could not be made -- the answer stands."""
    cited = set(_CITED.findall(answer))
    if not cited:
        return None
    carried = [carried_files(item) for item in items]
    known = set().union(*carried)
    read = [item for item, files in zip(items, carried, strict=True) if files & cited] or items
    content = f"Answer:\n{answer}\n\nPassages:\n" + "\n\n".join(i.content for i in read)
    try:
        reply = complete(system=CHECK_SYSTEM, content=content,
                         schema=strict_response_schema(_Claims), name="answer_check")
        written = _Claims.model_validate_json(reply)
    except Exception as error:  # noqa: BLE001 -- an unchecked answer still goes out
        _log.warning("answer not checked -- %s", str(error)[:200])
        return None
    # A claim the answer does not hold is the check's own writing: it proves nothing.
    held = folded(answer)
    return [Claim(c.text, supported, None if supported else c.correction, c.files)
            for c in written.claims if folded(c.text) and folded(c.text) in held
            for supported in [c.supported and not _cites_unread(answer, c.text, known)]]


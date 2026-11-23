"""Copies and versions of one document, told by what they say, never by their names.

A name's `final`, `v3` or `(1)` differs from one collection to the next; content does not. Two
documents are one family when their files are the same bytes (the content hash), or when the
sets of what their passages say -- each heading and each line once -- overlap by VERSION_OVERLAP
or more (Jaccard), or when the smaller one's lines are nearly all in the larger (CONTAINED) and
they still overlap by CONTAINED_OVERLAP -- an original and the same text with an annex appended.
Lines, not whole passages: passages are packed to a size, so one paragraph
rewritten moves where every later passage starts, and two readings of an outline can put a heading
in a passage's path or in its words. MinHash picks the pairs worth measuring -- signatures cut
into bands, two documents sharing a band are measured -- so a space is not measured pair by pair;
Jaccard over the lines decides.
"""
from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any, NamedTuple

import numpy as np

VERSION_OVERLAP = 0.8
"""How much of two documents' passages must be the same (Jaccard) for them to be versions."""
CONTAINED = 0.96
"""How much of the smaller document's lines the larger must hold to be the same text grown."""
CONTAINED_OVERLAP = 0.4
"""The Jaccard a contained pair still needs: a page quoted inside a long report is not a version."""
PERMUTATIONS = 128
"""How many hashes a document's signature holds."""
BANDS = 64
"""The signature cut into this many bands: two documents sharing one are measured. Two hashes a
band finds nearly every pair at CONTAINED_OVERLAP; four missed nearly half of them."""
KEYS_RULE = "lines"
"""Names what a document's `version_keys` hash (`passage_keys`): stored with them, so keys
hashed another way are hashed again from the passages."""
VERSIONS_RULE = (f"{KEYS_RULE}/minhash{PERMUTATIONS}x{BANDS}/at{VERSION_OVERLAP}"
                 f"/in{CONTAINED}at{CONTAINED_OVERLAP}")
"""Names how families are found: stored on each document grouped, so a change regroups all."""

_PRIME = (1 << 31) - 1
"""Hashes are taken modulo this prime, small enough that `a * h + b` fits in 64 bits."""
_RANDOM = np.random.default_rng(0)
_A = _RANDOM.integers(1, _PRIME, PERMUTATIONS, dtype=np.int64)
_B = _RANDOM.integers(0, _PRIME, PERMUTATIONS, dtype=np.int64)


class Measured(NamedTuple):
    """A document as it is grouped: its file's content hash and its passages' hashes
    (`passage_hashes`)."""

    id: str
    content_hash: str | None
    hashes: frozenset[int]


class Signed(NamedTuple):
    """A document as candidates are picked: its content hash and its MinHash `signature`."""

    id: str
    content_hash: str | None
    signature: Sequence[int]


class Member(NamedTuple):
    """A document of a family as a row shows it."""

    document_id: str
    file: str
    modified: float | None


@dataclass(frozen=True, slots=True)
class VersionOf:
    """Another document of a shown one's family, folded under it."""

    document_id: str
    file: str
    modified: float | None
    latest: bool
    """Whether it is the family's last changed file."""


def passage_keys(text: str) -> list[str]:
    """What a passage says as it is compared: each heading of its path and each of its lines,
    spacing made one -- without the file name the path opens with (`graph_chunks`), since a
    renamed copy says the same."""
    head, _, said = text.partition("\n")
    _, _, headings = head.partition(" > ")
    return [" ".join(line.split()) for line in [*headings.split(" > "), *said.split("\n")]]


def passage_hashes(texts: Iterable[str]) -> list[int]:
    """Each of the passages' `passage_keys` as a signed 64-bit hash, once each, in order: what
    two documents are compared by, small enough to keep on the document."""
    keys = {key for text in texts for key in passage_keys(text)}
    return sorted(int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big",
                                 signed=True) for key in keys if key)


def signature(hashes: Iterable[int]) -> list[int]:
    """A set of passage hashes as PERMUTATIONS numbers: the share of numbers two sets have in
    common estimates their Jaccard. Empty for no passage."""
    hashed = np.array(sorted({h % _PRIME for h in hashes}), dtype=np.int64)
    if not hashed.size:
        return []
    return [int(v) for v in ((np.outer(_A, hashed) + _B[:, None]) % _PRIME).min(axis=1)]


def minhash(texts: Iterable[str]) -> tuple[int, ...]:
    """The MinHash signature of a document's passage texts."""
    return tuple(signature(passage_hashes(texts)))


def overlap(a: Collection[object], b: Collection[object]) -> float:
    """The Jaccard of two documents' passages; nothing overlaps with nothing."""
    one, two = set(a), set(b)
    union = len(one | two)
    return len(one & two) / union if union else 0.0


def related(a: Collection[object], b: Collection[object]) -> bool:
    """Whether two documents' passages make them versions: overlapping by VERSION_OVERLAP, or
    the smaller held CONTAINED by the larger while overlapping by CONTAINED_OVERLAP."""
    one, two = set(a), set(b)
    shared, union, smaller = len(one & two), len(one | two), min(len(one), len(two))
    if not union:
        return False
    jaccard = shared / union
    return jaccard >= VERSION_OVERLAP or (
        jaccard >= CONTAINED_OVERLAP and shared / smaller >= CONTAINED)


Pair = tuple[str, str]


def candidates(docs: Sequence[Signed], fresh: Collection[str]) -> tuple[set[Pair], set[Pair]]:
    """The pairs of `docs` touching a `fresh` document worth a look: copies (the same content
    hash), and the others to measure -- those sharing a band of their signatures. Each pair
    once, its ids in order."""
    new = set(fresh)

    def touching(one: str, two: str) -> bool:
        return one != two and (one in new or two in new)

    copies: set[Pair] = set()
    by_hash: dict[str, list[str]] = {}
    for doc in docs:
        if doc.content_hash:
            by_hash.setdefault(doc.content_hash, []).append(doc.id)
    for ids in by_hash.values():
        copies.update(p for p in combinations(sorted(ids), 2) if touching(*p))
    # ponytail: a band shared by many documents (one template filled a thousand times) is
    # measured pair by pair; cap a bucket if a space of such templates grows slow.
    rows = PERMUTATIONS // BANDS
    buckets: dict[tuple[int, tuple[int, ...]], list[str]] = {}
    for doc in docs:
        if len(doc.signature) == PERMUTATIONS:
            for band in range(BANDS):
                key = (band, tuple(doc.signature[band * rows:(band + 1) * rows]))
                buckets.setdefault(key, []).append(doc.id)
    measure = {(min(one, two), max(one, two)) for bucket in buckets.values()
               for one, two in combinations(bucket, 2) if touching(one, two)}
    return copies, measure - copies


def confirmed(copies: set[Pair], measure: set[Pair],
              hashes: Mapping[str, Collection[int]]) -> list[Pair]:
    """The copies, and the pairs measured whose passages are `related`."""
    return sorted(copies | {(one, two) for one, two in measure
                            if related(hashes.get(one, ()), hashes.get(two, ()))})


def version_pairs(docs: Sequence[Measured]) -> list[Pair]:
    """Every pair of `docs` that are copies or versions, each once, its ids in order."""
    signed = [Signed(d.id, d.content_hash, signature(d.hashes)) for d in docs]
    copies, measure = candidates(signed, [d.id for d in docs])
    return confirmed(copies, measure, {d.id: d.hashes for d in docs})


def joined(pairs: Iterable[tuple[str, str]]) -> list[set[str]]:
    """The families `pairs` make: each set of documents reached from one another."""
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for one, two in pairs:
        parent[root(one)] = root(two)
    out: dict[str, set[str]] = {}
    for x in list(parent):
        out.setdefault(root(x), set()).add(x)
    return sorted(out.values(), key=min)


def families(docs: Sequence[Measured]) -> list[set[str]]:
    """The copies and versions among `docs`, each family a set of two or more."""
    return joined(version_pairs(docs))


def by_document(pairs: Iterable[tuple[Member, Member]]) -> dict[str, tuple[Member, ...]]:
    """Each document of a family, by id, to its whole family (in id order), from the pairs the
    graph joins."""
    held = list(pairs)
    members = {m.document_id: m for pair in held for m in pair}
    out: dict[str, tuple[Member, ...]] = {}
    for family in joined((one.document_id, two.document_id) for one, two in held):
        whole = tuple(members[i] for i in sorted(family))
        out.update({i: whole for i in family})
    return out


def latest(family: Sequence[Member]) -> Member:
    """The family's last changed file; one with no time is older than any with one. Copies
    changed at once are told apart by their names, never by ids a rebuild gives anew."""
    return max(family, key=lambda m: (m.modified is not None, m.modified or 0.0, m.file,
                                      m.document_id))


def versions_of(family: Sequence[Member], shown: Collection[str]) -> list[VersionOf]:
    """The members of `family` not `shown`, last changed first, the family's latest marked."""
    newest = latest(family).document_id
    return [VersionOf(m.document_id, m.file, m.modified, m.document_id == newest)
            for m in sorted(family, key=lambda m: (-(m.modified or 0.0), m.file))
            if m.document_id not in shown]


def family_key(family: Mapping[str, Sequence[Member]], document: str) -> str:
    """What a document is told apart by once families fold: its family's first id, else its own."""
    members = family.get(document)
    return members[0].document_id if members else document


def fold_versions(ranked: Sequence[Any], family: Mapping[str, Sequence[Member]],
                  only: frozenset[str] | None = None) -> list[Any]:
    """`ranked` with each family (`versions.by_document`) shown by its best-ranked member: the
    passages of its other members are left out, so a document's copies take no place of the
    documents asked for and the answer reads its words once. A question scoped to documents
    (`only`) folds nothing: every document in it was named."""
    if only is not None:
        return list(ranked)
    standing: dict[str, str] = {}
    return [passage for passage in ranked
            if standing.setdefault(family_key(family, passage["document_id"]),
                                   passage["document_id"]) == passage["document_id"]]

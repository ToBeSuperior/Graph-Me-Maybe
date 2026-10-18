"""How much a passage weighs in its document, and how alike two documents are.

Both are read off the passages' vectors alone -- nothing is asked of a model. A passage's
importance is how central it is to its document; two documents are as alike as the passages
of each that come nearest the other, each pair weighed by how much both passages matter to
their own documents. Each rule is stored beside what it measured, so a change in how either
is measured measures again.
"""
from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

import numpy as np

IMPORTANCE_RULE = "centroid-cosine/minmax/centroid-kept"
"""Names how a passage's importance in its document is measured, and that the document's
centroid is kept beside it; stored on each passage, so a document measured by another rule
is measured again."""

def centroid(vectors: Sequence[Sequence[float]]) -> list[float]:
    """What a document is mostly about: the mean of its passages' directions, of length 1."""
    matrix = _unit(vectors)
    centre = matrix.mean(axis=0)
    return [float(v) for v in centre / max(float(np.linalg.norm(centre)), 1e-12)]


def importance(vectors: Sequence[Sequence[float]]) -> list[float]:
    """How central each of a document's passages is to it, 0 to 1: the cosine of its vector
    to the document's centroid, scaled so the least central passage is 0 and the most is 1.
    A document of one passage, or of passages all alike, has each at 1."""
    if not vectors:
        return []
    cosine = _unit(vectors) @ np.asarray(centroid(vectors))
    spread = float(cosine.max() - cosine.min())
    if spread < 1e-9:
        return [1.0] * len(vectors)
    return [round(float(v), 4) for v in (cosine - cosine.min()) / spread]


def _unit(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=float)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True).clip(min=1e-12)
    return matrix


def relatedness(nearest: Sequence[tuple[str, str, float, float, float]]) -> float:
    """How related two documents are, the same whichever is asked first. `nearest` pairs
    each passage of either document with the passage of the other nearest it: (passage of
    one, passage of the other, cosine, importance of each). A pair both passages chose is
    counted once. The score is the pairs' cosines' mean, each weighted by both passages'
    importance -- a cosine still, with what both documents are mostly about counting most.
    Pairs that all weigh nothing count alike."""
    pairs = {(one, other): (cosine, weight_one * weight_other)
             for one, other, cosine, weight_one, weight_other in nearest}
    if not pairs:
        return 0.0
    total = sum(weight for _, weight in pairs.values())
    if total <= 0:
        return round(fmean(cosine for cosine, _ in pairs.values()), 4)
    return round(sum(cosine * weight for cosine, weight in pairs.values()) / total, 4)

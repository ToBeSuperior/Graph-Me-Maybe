"""The embedders a graph space's passages and questions are turned into vectors with."""
from __future__ import annotations

import contextlib
import threading
from abc import abstractmethod
from array import array
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Generic, TypeVar

from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.embeddings.base import Embedder

from graphme.domain.ids import sha256_bytes
from graphme.files import write_atomic

V = TypeVar("V")


class Remembered(Generic[V]):
    """What was worked out, by what it was for, shared by every reader in the process: the
    latest `most` kept (none: only while it runs), and what is being worked out right now
    worked out once, the others asking at the same time handed what it returned (or raised)."""

    def __init__(self, most: int) -> None:
        self._most = most
        self._kept: OrderedDict[Hashable, V] = OrderedDict()
        self._flights: dict[Hashable, Future[V]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, work: Callable[[], V],
            keep: Callable[[V], bool] = lambda _: True) -> V:
        with self._lock:
            if key in self._kept:
                self._kept.move_to_end(key)
                return self._kept[key]
            flight = self._flights.get(key)
            leads = flight is None
            if flight is None:
                flight = self._flights[key] = Future()
        if leads:
            try:
                value = work()
                with self._lock:
                    if keep(value):
                        self._kept[key] = value
                        while len(self._kept) > self._most:
                            self._kept.popitem(last=False)
                flight.set_result(value)
            except BaseException as error:
                flight.set_exception(error)
            finally:
                with self._lock:
                    del self._flights[key]
        return flight.result()


QUERY_VECTORS: Remembered[list[float]] = Remembered(64)
"""A question's vectors, by the embedder's address, model and the text sent: a question over
several workspaces is embedded once, not once a reader and again for its links."""


class BatchEmbedder(Embedder):
    """Embeds a question, and many passages in one call -- a whole space one call a passage
    takes far too long."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIBatchEmbeddings(OpenAIEmbeddings, BatchEmbedder):
    """OpenAI's embeddings endpoint, or a provider's compatible one (`base_url`). Vectors are
    asked for as floats -- the client otherwise asks for base64, which a compatible layer need
    not speak -- and put back in the order the texts were sent. A question is written after
    `query_prefix`, the instruction the model was trained to read one with; a passage goes as
    it is."""

    def __init__(self, *args: Any, query_prefix: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.query_prefix = query_prefix

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            input=texts, model=self.model, encoding_format="float")
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        asked = self.query_prefix + text
        return QUERY_VECTORS.get((str(self.client.base_url), self.model, asked),
                                 lambda: self.embed_texts([asked])[0])




class KeptVectors(BatchEmbedder):
    """A passage's vector kept on disk by the model and the passage's text: a passage
    embedded once is not embedded again -- an edited document's unchanged passages, put in
    under new ids, keep the vectors they had. A question is embedded as it always is. A kept
    vector of another size than `dimensions` is embedded again."""

    def __init__(self, embedder: BatchEmbedder, folder: Path, *, model: str,
                 dimensions: int | None = None) -> None:
        super().__init__()
        self._embedder = embedder
        self._folder = Path(folder)
        self._model = model
        self._dimensions = dimensions

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        return self._embedder.embed_query(text, **kwargs)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        keys = [vector_key(self._model, text) for text in texts]
        found = {key: vector for key in set(keys) if (vector := self._read(key)) is not None}
        missing = list(dict.fromkeys(t for t, key in zip(texts, keys, strict=True)
                                     if key not in found))
        if missing:
            for text, vector in zip(missing, self._embedder.embed_texts(missing), strict=True):
                key = vector_key(self._model, text)
                found[key] = vector
                self._write(key, vector)
        return [found[key] for key in keys]

    def _read(self, key: str) -> list[float] | None:
        try:
            vector = list(array("d", (self._folder / f"{key}.vec").read_bytes()))
        except (OSError, ValueError):
            return None
        if not vector or (self._dimensions is not None and len(vector) != self._dimensions):
            return None
        return vector

    def _write(self, key: str, vector: list[float]) -> None:
        with contextlib.suppress(OSError):
            write_atomic(self._folder / f"{key}.vec", array("d", vector).tobytes(),
                         suffix=".part", sync=False)


def vector_key(model: str, text: str) -> str:
    """What a passage's vector is kept under: the model and the text sent."""
    return sha256_bytes(f"{model}\0{text}".encode())


def sweep_vectors(folder: Path, *, model: str, texts: Iterable[str], before: float) -> int:
    """Every kept vector but `model`'s of `texts` (the space's passages, as sent) deleted,
    unless written since `before`, with any write cut short: how many."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    keep = {vector_key(model, text) for text in texts}
    removed = 0
    for path in [*folder.glob("*.vec"), *folder.glob("*.part")]:
        try:
            stale = path.stat().st_mtime < before
        except OSError:
            continue
        if stale and (path.suffix == ".part" or path.stem not in keep):
            path.unlink(missing_ok=True)
            removed += 1
    return removed

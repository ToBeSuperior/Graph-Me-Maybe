"""The server without a browser, a renderer, a model or Neo4j. Invented documents only."""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme import api
from graphme.config import Settings
from graphme.server import create_app
from graphme.server.state import Demo
from tests.fakes.documents import named_document as _doc

SETTINGS = Settings(profile="test", embedding_provider="hash")


class FakeSpace:
    """The graph space, held in memory: what the build and the routes ask of it."""

    def __init__(self) -> None:
        self.put: list[str] = []
        self.held: dict[str, str] = {}
        """document id -> file name"""
        self.paths: dict[str, str] = {}
        """document id -> the file it was read from"""
        self.hashes: set[str] = set()
        self.deleted: list[list[str]] = []
        self.relocated: list[tuple[str, str]] = []
        self.asked: list[list[str] | None] = []
        """The documents each export asked for; None is all."""
        self.documents: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.similar: list[dict[str, Any]] = []
        self.crops: set[str] = set()
        """The crop assets the space's nodes name."""

    def put_document(self, extracted: Any) -> None:
        self.put.append(extracted.document.id)
        self.held[extracted.document.id] = extracted.document.file_name
        self.paths[extracted.document.id] = str(extracted.document.source_path)

    def relocate(self, content_hash: str, source_path: str) -> None:
        self.relocated.append((content_hash, source_path))

    def read_from(self, source_path: str) -> set[str]:
        return {i for i, path in self.paths.items() if path == source_path and i in self.held}

    def identities(self) -> tuple[()]:
        return ()

    def mentions(self) -> tuple[()]:
        return ()

    def put_links(self, links: Any, *, of: Any) -> None:
        pass

    def unlinked(self, rule: Any) -> set[str]:
        return set()

    def mark_linked(self, documents: Any, rule: Any) -> None:
        pass

    def contents(self, hashes: Any) -> set[str]:
        return {h for h in hashes if h in self.hashes}

    def source_path(self, document_id: str) -> str | None:
        return self.paths.get(document_id)

    def files(self, ids: Any) -> dict[str, str]:
        return {i: self.held[i] for i in ids if i in self.held}

    def delete_documents(self, ids: Any) -> None:
        self.deleted.append(list(ids))
        for i in ids:
            self.held.pop(i, None)

    def reset(self) -> int:
        emptied = len(self.held)
        self.held.clear()
        return emptied

    def document_map(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return self.documents, self.links

    def similar_links(self) -> list[dict[str, Any]]:
        return self.similar

    def document_graph(self, document_id: str) -> dict[str, list[dict[str, Any]]]:
        if document_id not in self.held:
            return {"nodes": [], "relationships": []}
        from graphme.adapters.export.neo4j import graph_records

        return graph_records(_doc(document_id, self.held[document_id]))

    def chunk_spans(self, document_id: str) -> list[dict[str, Any]]:
        return []

    def corpus(self, document_ids: Any = None, passages: bool = True) -> dict[str, Any]:
        from graphme.adapters.export.neo4j import graph_records

        self.asked.append(document_ids)
        return {**graph_records(_doc("a", "memo.pdf")), "chunks": [], "covers": [], "indexes": []}

    def corpus_rows(self, document_ids: Any = None) -> Any:
        from tests.fakes.corpus import rows_of

        return rows_of(self.corpus(document_ids))

    def crop_hashes(self) -> set[str]:
        return set(self.crops)

    def close(self) -> None:
        pass


class FakeAdmin:
    """`neo4j-admin` without Docker: a dump is four bytes, an upload is remembered."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    def dump(self, bundle: Path, target: Path) -> None:
        assert (bundle / "import.sh").is_file()
        target.write_bytes(b"DUMP")

    def upload(self, dump: Path, *, uri: str, user: str, password: str) -> str:
        self.uploads.append({"dump": dump.read_bytes(), "uri": uri, "user": user,
                             "password": password})
        return "upload complete"


@pytest.fixture
def space() -> FakeSpace:
    return FakeSpace()


@pytest.fixture
def admin() -> FakeAdmin:
    return FakeAdmin()


@pytest.fixture
def demo(
    tmp_path: Path, space: FakeSpace, admin: FakeAdmin, monkeypatch: pytest.MonkeyPatch
) -> Demo:
    """A server state over the fake space and admin, its folder under the test's own."""
    monkeypatch.setattr(api, "create_neo4j_admin", lambda settings: admin)
    # Its data under the test's own folder: the shared settings name the working directory's.
    settings = SETTINGS.model_copy(update={"data_dir": tmp_path / "data"})
    made = Demo(settings, renderer=None, root=tmp_path / "server")  # type: ignore[arg-type]
    made.graph._space = space  # type: ignore[assignment]
    made.admin = lambda: admin  # type: ignore[assignment,method-assign,return-value]
    return made


@pytest.fixture
def client(demo: Demo) -> TestClient:
    """The API alone (no web app), asked from this machine as the web app asks."""
    return TestClient(create_app(demo), base_url="http://127.0.0.1:8765")


def until(check: Callable[[], Any], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.02)
    return bool(check())


def read_as(doc: str, name: str) -> Callable[[Any], None]:
    """A reading that takes no model: the session ends holding an invented document."""

    def run(session: Any) -> None:
        session.extracted = _doc(doc, name)
        session.state = "done"

    return run

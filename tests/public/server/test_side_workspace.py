"""A workspace not open, read beside the open one: its own Neo4j, one at a time, let go when idle.

What the app shows does not change; an MCP client reads another workspace through `?workspace=`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.storage import local_neo4j
from graphme.adapters.storage.graph_rag import GraphNotEmbedded
from graphme.server import create_app, state
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST
from tests.public.server.conftest import SETTINGS, FakeSpace, until

BASE = "http://127.0.0.1:8765"
stopped: list[Path] = []


class _Rag:
    """Passages embedded with another search model: the hybrid search refuses, text alone finds."""

    def __init__(self) -> None:
        self.text_only = False

    def find(self, question: str, *, documents: int) -> list[Any]:
        if not self.text_only:
            raise GraphNotEmbedded("passages were embedded with another model")
        return []


class _Graph:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.opened_space = self._space = FakeSpace()
        self._space.documents = [{"id": "side-doc", "label": "Invented memo.docx"}]
        self._rag = _Rag()
        self.closed = False

    def graph_space(self) -> FakeSpace:
        return self._space

    def rag(self) -> _Rag:
        return self._rag

    def forget_rag(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def demo_(home: Path, monkeypatch: pytest.MonkeyPatch) -> Demo:
    monkeypatch.setattr(state, "GraphMe", _Graph)
    monkeypatch.setattr(local_neo4j, "stop_graph", lambda graph: stopped.append(graph))
    stopped.clear()
    return Demo(SETTINGS.model_copy(update={"local_neo4j": home}), renderer=None,  # type: ignore[arg-type]
                root=home / "server")


def test_another_workspace_is_read_beside_and_the_app_keeps_its_own(
    demo_: Demo, home: Path
) -> None:
    made = demo_.workspaces.create("Invented project")  # type: ignore[union-attr]
    open_one = demo_.graph

    assert demo_.reader(None) is open_one and demo_.reader(FIRST) is open_one
    side = demo_.reader(made["id"])

    assert side is not open_one
    assert side.settings.local_graph == home / "workspaces" / made["id"]
    assert demo_.reader(made["id"]) is side  # kept while asked
    assert demo_.graph is open_one and demo_.workspaces.active == FIRST  # type: ignore[union-attr]


def test_one_workspace_beside_at_a_time(demo_: Demo, home: Path) -> None:
    first = demo_.workspaces.create("Invented one")  # type: ignore[union-attr]
    second = demo_.workspaces.create("Invented two")  # type: ignore[union-attr]
    one = demo_.reader(first["id"])

    two = demo_.reader(second["id"])

    assert two is not one and one.closed  # type: ignore[attr-defined]
    assert stopped == [home / "workspaces" / first["id"]]


def test_a_workspace_beside_is_let_go_when_nobody_asks(
    demo_: Demo, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state, "SIDE_IDLE_SECONDS", 0.2)
    made = demo_.workspaces.create("Invented project")  # type: ignore[union-attr]
    side = demo_.reader(made["id"])

    assert until(lambda: side.closed and stopped == [home / "workspaces" / made["id"]], 5)  # type: ignore[attr-defined]


def test_opening_the_workspace_read_beside_lets_it_go_first(demo_: Demo) -> None:
    made = demo_.workspaces.create("Invented project")  # type: ignore[union-attr]
    side = demo_.reader(made["id"])

    demo_.switch_workspace(made["id"])

    assert side.closed  # type: ignore[attr-defined]
    assert demo_.graph is not side and demo_.workspaces.active == made["id"]  # type: ignore[union-attr]


def test_reading_beside_over_http(demo_: Demo) -> None:
    made = demo_.workspaces.create("Invented project")  # type: ignore[union-attr]
    client = TestClient(create_app(demo_), base_url=BASE)

    library = client.get("/v1/documents", params={"workspace": made["id"]}).json()
    found = client.post("/v1/search", json={"question": "invented", "workspace": made["id"]})
    nowhere = client.get("/v1/graph/map", params={"workspace": "no-such-workspace"})

    assert [(i["name"], i["in_space"]) for i in library["items"]] == [("Invented memo.docx", True)]
    # Embedded with another search model: searched by its words alone, and said so.
    assert found.status_code == 200 and found.json()["text_only"] is True
    assert nowhere.status_code == 404

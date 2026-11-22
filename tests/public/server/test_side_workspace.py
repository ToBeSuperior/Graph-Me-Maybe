"""A workspace not open, read beside the open one: its scope over the open graph's connection,
nothing started or stopped.

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
from tests.public.server.conftest import SETTINGS, FakeSpace

BASE = "http://127.0.0.1:8765"
stopped: list[Path] = []


class _Rag:
    """Passages embedded with another search model: the hybrid search refuses, text alone finds."""

    def __init__(self) -> None:
        self.text_only = False

    def find(self, question: str, *, documents: int,
            history: Any = (), only: Any = None) -> list[Any]:
        if not self.text_only:
            raise GraphNotEmbedded("passages were embedded with another model")
        return []

    def last_queries(self, question: str, history: Any = ()) -> tuple[str, ...]:
        return (question,)


class _Graph:
    def __init__(self, settings: Any, *, base: _Graph | None = None, took: bool = False) -> None:
        self.settings = settings
        self.base, self.took = base, took
        self.opened_space = self._space = FakeSpace()
        self._space.documents = [{"id": "side-doc", "label": "Invented memo.docx"}]
        self._rag = _Rag()
        self.closed = False

    def held_to(self, settings: Any, *, take: bool = False) -> _Graph:
        return type(self)(settings, base=self, took=take)

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


def test_another_workspace_is_read_over_the_open_graph_s_connection(
    demo_: Demo, home: Path
) -> None:
    made = demo_.workspaces.create("Invented project")  # type: ignore[union-attr]
    open_one = demo_.graph

    assert demo_.reader(None) is open_one and demo_.reader(FIRST) is open_one
    side = demo_.reader(made["id"])

    assert side.base is open_one and not side.took  # type: ignore[attr-defined]
    assert side.settings.workspace == made["id"]
    assert side.settings.local_graph == home / "workspaces" / made["id"]  # its crops
    assert demo_.reader(made["id"]) is side  # kept while the open graph is the same
    assert demo_.graph is open_one and demo_.workspaces.active == FIRST  # type: ignore[union-attr]
    assert stopped == []


def test_several_workspaces_are_read_beside_at_once(demo_: Demo) -> None:
    first = demo_.workspaces.create("Invented one")  # type: ignore[union-attr]
    second = demo_.workspaces.create("Invented two")  # type: ignore[union-attr]
    one = demo_.reader(first["id"])

    two = demo_.reader(second["id"])

    assert two is not one and not one.closed  # type: ignore[attr-defined]
    assert demo_.reader(first["id"]) is one
    assert stopped == []


def test_a_workspace_beside_follows_the_open_graph_after_a_switch(demo_: Demo) -> None:
    first = demo_.workspaces.create("Invented one")  # type: ignore[union-attr]
    second = demo_.workspaces.create("Invented two")  # type: ignore[union-attr]
    side = demo_.reader(first["id"])

    demo_.switch_workspace(second["id"])
    again = demo_.reader(first["id"])

    assert again is not side and again.base is demo_.graph  # type: ignore[attr-defined]
    assert demo_.reader(second["id"]) is demo_.graph


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

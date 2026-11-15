"""Every workspace in the home's one Neo4j: the space is opened on the home's graph whatever
workspace is open, held to that workspace's scope, and another workspace is the same
connection held to another scope. No Neo4j: invented folders and fakes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphme import api, runtime
from graphme.adapters.storage import neo4j_space
from graphme.adapters.storage.workspace_scope import WorkspaceScope
from graphme.config import Settings

SETTINGS = Settings(profile="test", embedding_provider="hash")


def test_a_workspace_s_space_is_the_home_s_one_neo4j_held_to_its_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[dict[str, Any]] = []
    opened: list[Settings] = []
    monkeypatch.setattr(runtime, "ensure_local_neo4j",
                        lambda root, memory, **given: started.append(given) or "bolt://x:1")
    monkeypatch.setattr(runtime, "neo4j_password", lambda root: f"secret of {root.name}")
    space = _Space(_Driver(), WorkspaceScope("0a1b"))
    monkeypatch.setattr(neo4j_space.Neo4jGraphSpace, "from_settings",
                        classmethod(lambda cls, settings: opened.append(settings) or space))
    relabelled: list[Any] = []
    monkeypatch.setattr(runtime, "move_home_graph", relabelled.append)
    home = tmp_path / "home"
    settings = SETTINGS.model_copy(update={
        "local_neo4j": home, "local_graph": home / "workspaces" / "0a1b", "workspace": "0a1b"})

    assert runtime.create_graph_space(settings) is space

    assert started == [{}]  # no workspace folder's own graph
    assert opened[0].neo4j_password == "secret of home" and opened[0].workspace == "0a1b"
    assert relabelled == [space.driver]  # a graph from before workspaces: the default's


class _Driver:
    def __init__(self) -> None:
        self.closed = False

    def verify_connectivity(self) -> None:
        pass

    def execute_query(self, query: str, *_: Any, **__: Any) -> Any:
        return [], None, None

    def close(self) -> None:
        self.closed = True


def test_the_space_from_settings_is_its_workspace_s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neo4j_space.GraphDatabase, "driver", lambda *a, **k: _Driver())

    space = neo4j_space.Neo4jGraphSpace.from_settings(SETTINGS.model_copy(
        update={"workspace": "0a1b"}))

    assert space.scope == WorkspaceScope("0a1b")


class _Space:
    def __init__(self, driver: _Driver, scope: WorkspaceScope) -> None:
        self.driver, self.scope = driver, scope

    def within(self, scope: WorkspaceScope) -> _Space:
        return _Space(self.driver, scope)

    def close(self) -> None:
        self.driver.close()


def test_another_workspace_is_the_same_connection_held_to_its_scope() -> None:
    driver = _Driver()
    graph = api.GraphMe(SETTINGS)
    graph._space = _Space(driver, WorkspaceScope("default"))  # type: ignore[assignment]

    beside = graph.held_to(SETTINGS.model_copy(update={"workspace": "0a1b"}))

    assert beside.opened_space.scope == WorkspaceScope("0a1b")  # type: ignore[union-attr]
    assert beside.opened_space.driver is driver  # type: ignore[union-attr]
    beside.close()
    assert not driver.closed  # still the open graph's


def test_a_workspace_switched_to_takes_the_connection_over() -> None:
    driver = _Driver()
    graph = api.GraphMe(SETTINGS)
    graph._space = _Space(driver, WorkspaceScope("default"))  # type: ignore[assignment]

    moved = graph.held_to(SETTINGS.model_copy(update={"workspace": "0a1b"}), take=True)
    graph.close()  # the one left lets go of nothing it handed over

    assert not driver.closed
    moved.close()
    assert driver.closed


class _Deleting(_Driver):
    """Nodes of a workspace deleted a batch at a time: two batches, then none."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []
        self.left = {"C_0a1b": [3, 0], "W_0a1b": [5000, 2, 0]}

    def execute_query(self, query: str, *_: Any, **__: Any) -> Any:
        import neo4j

        self.sent.append(query)
        label = next((k for k in self.left if f"(n:{k})" in query), None)
        rows = [{"gone": self.left[label].pop(0)}] if label else []
        return neo4j.EagerResult(rows, None, [])  # type: ignore[arg-type]


def test_a_deleted_workspace_leaves_the_one_neo4j_with_its_schema() -> None:
    driver = _Deleting()
    space = neo4j_space.Neo4jGraphSpace.__new__(neo4j_space.Neo4jGraphSpace)
    space.driver = driver  # type: ignore[assignment]

    space.drop_workspace(WorkspaceScope("0a1b"))

    deletes = [q for q in driver.sent if "DETACH DELETE" in q]
    assert len(deletes) == 5 and all("W_0a1b" in q or "C_0a1b" in q for q in deletes)
    assert "DROP CONSTRAINT doc_node_id_0a1b IF EXISTS" in driver.sent
    assert "DROP INDEX chunk_text_0a1b IF EXISTS" in driver.sent
    assert "DROP INDEX chunk_embedding_0a1b IF EXISTS" in driver.sent
    assert not any("DocNode" in q or ":Chunk" in q for q in driver.sent)

"""At the first opening of the graph, each workspace whose graph is still in its own folder is
copied into the one Neo4j once, and marked; a failed copy leaves it unmarked, its folder as it
was, and says so. No Neo4j: the folder's graph and the copy are fakes. Invented names only."""
# ruff: noqa: F811 -- the fixtures are test_workspaces's, used by name
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.storage.workspace_move import Counts, MoveMismatch
from graphme.adapters.storage.workspace_scope import WorkspaceScope
from graphme.server import create_app, state
from graphme.server.jobs import CorpusBuild
from graphme.server.state import Demo
from graphme.server.workspaces import Workspaces
from tests.public.server.test_workspaces import BASE, home, workspace_demo  # noqa: F401

opened: list[Path] = []
copied: list[tuple[str, Any, WorkspaceScope]] = []


@pytest.fixture(autouse=True)
def _fake_move(monkeypatch: pytest.MonkeyPatch) -> None:
    opened.clear()
    copied.clear()

    @contextmanager
    def folder_graph(settings: Any, folder: Path) -> Iterator[str]:
        opened.append(folder)
        yield f"graph of {folder.name}"

    def copy(source: str, space: Any, scope: WorkspaceScope) -> Counts:
        copied.append((source, space, scope))
        if scope.workspace_id.startswith("bad"):
            raise MoveMismatch("2 units read, 1 written")
        return Counts(nodes=2, relationships=1, chunks=1, covers=1)

    monkeypatch.setattr(state, "workspace_folder_graph", folder_graph)
    monkeypatch.setattr(state, "copy_graph", copy)


def _with_old_graph(spaces: Workspaces, name: str, workspace: str | None = None) -> str:
    made = spaces.create(name)
    if workspace is not None:  # an id chosen by the test, to fail the fake copy
        said = spaces.listed()
        said["items"][-1]["id"] = workspace
        spaces._write(said)
        made["id"] = workspace
    folder = spaces.graph(made["id"])
    assert folder is not None
    (folder / "neo4j-data").mkdir(parents=True)
    (folder / "neo4j-data" / "kept").write_text("invented store", encoding="utf-8")
    return str(made["id"])


def test_a_folder_s_graph_is_moved_once_and_marked(workspace_demo: Demo, home: Path) -> None:
    spaces = workspace_demo.workspace_registry()
    old = _with_old_graph(spaces, "Invented project")
    spaces.create("Invented new one")  # no graph of its own: nothing to move

    space = workspace_demo.space()
    workspace_demo.space()  # opened again: not moved again

    assert opened == [home / "workspaces" / old]
    assert copied == [(f"graph of {old}", space.driver, WorkspaceScope(old))]
    assert spaces.moved() == {old}
    assert (home / "workspaces" / old / "neo4j-data" / "kept").is_file()  # kept to be checked
    again = Demo(workspace_demo.settings, renderer=None,  # type: ignore[arg-type]
                 root=home / "server2")
    again.space()
    assert len(copied) == 1  # marked: a later start does not copy it again


def test_a_failed_move_leaves_the_workspace_on_its_folder_and_says_so(
    workspace_demo: Demo, home: Path
) -> None:
    spaces = workspace_demo.workspace_registry()
    bad = _with_old_graph(spaces, "Invented failing", workspace="bad0a1b")

    workspace_demo.space()

    assert spaces.moved() == set()
    assert (home / "workspaces" / bad / "neo4j-data" / "kept").is_file()
    said = TestClient(create_app(workspace_demo), base_url=BASE).get("/v1/workspaces").json()
    assert said["unmoved"] == {bad: "2 units read, 1 written"}


def test_no_move_while_a_build_runs(workspace_demo: Demo) -> None:
    spaces = workspace_demo.workspace_registry()
    _with_old_graph(spaces, "Invented project")
    workspace_demo.build = CorpusBuild(id="b", state="reading")

    workspace_demo.space()

    assert copied == [] and spaces.moved() == set()


def test_a_page_asking_while_a_move_runs_is_told_the_graph_is_starting(
    workspace_demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    spaces = workspace_demo.workspace_registry()
    _with_old_graph(spaces, "Invented project")
    moving, release = threading.Event(), threading.Event()

    def slow(source: str, space: Any, scope: WorkspaceScope) -> Counts:
        moving.set()
        release.wait(5)
        return Counts(nodes=2, relationships=1, chunks=1, covers=1)

    monkeypatch.setattr(state, "copy_graph", slow)
    threading.Thread(target=workspace_demo.space, daemon=True).start()  # the first opening
    assert moving.wait(5)
    try:
        with pytest.raises(state.GraphStarting):  # not the open graph, empty until moved
            workspace_demo.space(wait=0)
    finally:
        release.set()
    assert workspace_demo.space(wait=5) is not None

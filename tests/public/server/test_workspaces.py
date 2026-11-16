"""Workspaces: graphs kept apart in the home's one Neo4j, each its own scope; switching holds
the same connection to another scope, nothing started or stopped. No Neo4j: a workspace's graph
is a fake space. Invented names only."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.storage import local_neo4j
from graphme.adapters.storage.workspace_scope import DEFAULT_SCOPE, WorkspaceScope
from graphme.server import create_app, state
from graphme.server.jobs import Busy, CorpusBuild
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST, Workspaces
from tests.public.server.conftest import SETTINGS, FakeSpace

BASE = "http://127.0.0.1:8765"


class _Graph:
    """A workspace's graph as the server holds it, opened on a fake space; `held_to` is
    another workspace over its connection (`base`), handed over when `took`."""

    def __init__(self, settings: Any, *, base: _Graph | None = None, took: bool = False) -> None:
        self.settings = settings
        self.base, self.took = base, took
        self.opened_space = self._space = base._space if base is not None else FakeSpace()
        self.closed = False

    def held_to(self, settings: Any, *, take: bool = False) -> _Graph:
        return type(self)(settings, base=self, took=take)

    def graph_space(self) -> FakeSpace:
        return self._space

    def forget_rag(self) -> None:
        pass

    def delete(self, ids: Any) -> None:
        self._space.delete_documents(ids)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def workspace_demo(home: Path, monkeypatch: pytest.MonkeyPatch) -> Demo:
    monkeypatch.setattr(state, "GraphMe", _Graph)
    monkeypatch.setattr(local_neo4j, "stop_graph", lambda graph: stopped.append(graph))
    stopped.clear()
    return Demo(SETTINGS.model_copy(update={"local_neo4j": home}), renderer=None,  # type: ignore[arg-type]
                root=home / "server")


stopped: list[Path] = []


def test_a_home_starts_with_its_own_graph_as_the_one_workspace(home: Path) -> None:
    said = Workspaces(home).listed()

    assert said == {"active": FIRST, "mcp_enabled": True, "mcp_changes": False,
                    "items": [{"id": FIRST, "name": "Default", "mcp": False,
                               "kind": "documents", "account": None}]}
    assert Workspaces(home).graph(FIRST) is None  # the graph where it always was


def test_a_workspace_is_made_renamed_and_its_name_kept_its_own(home: Path) -> None:
    spaces = Workspaces(home)
    made = spaces.create("  Quality   audits ")

    assert made["name"] == "Quality audits"
    assert spaces.graph(made["id"]) == home / "workspaces" / made["id"]
    assert spaces.rename(made["id"], "Supplier audits")["name"] == "Supplier audits"
    with pytest.raises(ValueError):
        spaces.create("supplier AUDITS")  # another's name
    with pytest.raises(ValueError):
        spaces.rename(made["id"], "   ")
    assert Workspaces(home).item(made["id"])["name"] == "Supplier audits"  # kept on disk


def test_the_open_workspace_cannot_be_deleted_and_another_goes_with_its_graph(
    home: Path,
) -> None:
    spaces = Workspaces(home)
    made = spaces.create("Invented project")
    graph = spaces.graph(made["id"])
    assert graph is not None
    (graph / "neo4j-data").mkdir(parents=True)

    with pytest.raises(Busy):
        spaces.delete(FIRST)
    spaces.delete(made["id"])

    assert [i["id"] for i in spaces.listed()["items"]] == [FIRST] and not graph.exists()
    with pytest.raises(LookupError):
        spaces.delete(made["id"])


def test_switching_holds_the_same_connection_to_the_other_workspace(
    workspace_demo: Demo, home: Path
) -> None:
    old = workspace_demo.graph
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.jobs["old"] = CorpusBuild(id="old", state="done")

    workspace_demo.switch_workspace(made["id"])

    graph = workspace_demo.graph
    assert graph.base is old and graph.took  # type: ignore[attr-defined]
    assert workspace_demo.settings.workspace == made["id"] == graph.settings.workspace
    # The folder keeps only its crops and mail; the graph is the home's one Neo4j.
    assert workspace_demo.settings.local_graph == home / "workspaces" / made["id"]
    assert "old" not in workspace_demo.jobs  # the last build was the other graph's
    assert not old.closed and stopped == []  # type: ignore[attr-defined]
    assert Workspaces(home).active == made["id"]


def test_a_start_opens_the_workspace_open_when_the_app_ended(home: Path) -> None:
    made = Workspaces(home).create("Invented project")
    Workspaces(home).activate(made["id"])

    again = Demo(SETTINGS.model_copy(update={"local_neo4j": home}), renderer=None,  # type: ignore[arg-type]
                 root=home / "server")

    assert again.settings.local_graph == home / "workspaces" / made["id"]
    assert again.settings.workspace == made["id"]


def test_a_workspace_id_names_its_labels(home: Path) -> None:
    made = Workspaces(home).create("Invented project")

    assert WorkspaceScope(made["id"]).node_label == f"W_{made['id']}"
    assert WorkspaceScope(FIRST) == DEFAULT_SCOPE


def test_deleting_a_workspace_takes_its_scope_out_of_the_graph_then_its_folder(
    workspace_demo: Demo, home: Path
) -> None:
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    folder = home / "workspaces" / made["id"] / "assets"
    folder.mkdir(parents=True)
    space = workspace_demo.graph.graph_space()

    with pytest.raises(Busy):
        workspace_demo.delete_workspace(FIRST)  # the one open
    assert space.dropped == []  # type: ignore[attr-defined]
    workspace_demo.delete_workspace(made["id"])

    assert space.dropped == [WorkspaceScope(made["id"])]  # type: ignore[attr-defined]
    assert not folder.parent.exists() and stopped == []


def test_a_delete_waits_for_a_switch_under_way(workspace_demo: Demo) -> None:
    """A delete checked the workspace was not the one open while a switch onto it was under
    way, then dropped the graph the switch had just opened."""
    import threading

    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    space = workspace_demo.graph.graph_space()
    deleting = threading.Thread(target=workspace_demo.delete_workspace, args=(made["id"],))

    with workspace_demo.building:  # as a switch holds it
        deleting.start()
        deleting.join(0.3)
        assert space.dropped == []  # type: ignore[attr-defined]
    deleting.join(5)

    assert space.dropped == [WorkspaceScope(made["id"])]  # type: ignore[attr-defined]


def test_while_the_graph_db_opens_a_workspace_request_is_told_it_is_starting(
    workspace_demo: Demo, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first start moves every workspace's old graph in, minutes long, under the lock that
    opens the graph: a switch, a read beside, a delete or new keys waited it out."""
    import threading
    import time

    from graphme.server.jobs import GraphStarting

    monkeypatch.setattr(state, "SPACE_WAIT", 0.1)
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.graph.opened_space = None  # type: ignore[attr-defined]
    held, release = threading.Event(), threading.Event()

    def opening() -> None:
        with workspace_demo.opening_space:
            held.set()
            release.wait(3)

    threading.Thread(target=opening, daemon=True).start()
    held.wait(5)
    client = TestClient(create_app(workspace_demo), base_url=BASE)
    try:
        for ask in (lambda: workspace_demo.switch_workspace(made["id"]),
                    lambda: workspace_demo.reader(made["id"]),
                    lambda: workspace_demo.delete_workspace(made["id"]),
                    lambda: workspace_demo.reconfigure(
                        SETTINGS.model_copy(update={"local_neo4j": home}))):
            started = time.monotonic()
            with pytest.raises(GraphStarting):
                ask()
            assert time.monotonic() - started < 1.5
        said = client.post(f"/v1/workspaces/{made['id']}/open")
        assert said.status_code == 503 and said.headers.get("Retry-After")
    finally:
        release.set()
    assert workspace_demo.workspaces.active == FIRST  # type: ignore[union-attr]


def test_no_switch_while_a_build_runs(workspace_demo: Demo) -> None:
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.build = CorpusBuild(id="b", state="reading")

    with pytest.raises(Busy):
        workspace_demo.switch_workspace(made["id"])
    assert workspace_demo.workspaces.active == FIRST  # type: ignore[union-attr]


def test_a_switch_waits_out_the_embedding_a_workspace_opened_with(workspace_demo: Demo) -> None:
    """Opening a workspace embeds what it holds with the chosen search model, a second or two;
    a switch asked for meanwhile was refused as though a build ran."""
    import threading

    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    embedding = workspace_demo.build = CorpusBuild(id="e", kind="embed", state="embedding")
    threading.Timer(0.3, lambda: setattr(embedding, "state", "done")).start()

    workspace_demo.switch_workspace(made["id"])

    assert workspace_demo.workspaces.active == made["id"]  # type: ignore[union-attr]


def test_new_keys_keep_the_workspace_open(workspace_demo: Demo, home: Path) -> None:
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.switch_workspace(made["id"])

    workspace_demo.reconfigure(SETTINGS.model_copy(update={"local_neo4j": home}))

    assert workspace_demo.settings.local_graph == home / "workspaces" / made["id"]


def test_workspaces_are_listed_made_renamed_opened_and_deleted_over_http(
    workspace_demo: Demo,
) -> None:
    client = TestClient(create_app(workspace_demo), base_url=BASE)

    made = client.post("/v1/workspaces", json={"name": "Invented project"}).json()
    renamed = client.patch(f"/v1/workspaces/{made['id']}", json={"name": "Invented audits"})
    opened = client.post(f"/v1/workspaces/{made['id']}/open").json()
    refused = client.delete(f"/v1/workspaces/{made['id']}")
    elsewhere = client.delete(f"/v1/workspaces/{FIRST}",
                              headers={"origin": "https://elsewhere.invented"})
    deleted = client.delete(f"/v1/workspaces/{FIRST}").json()

    assert renamed.json()["name"] == "Invented audits"
    assert opened["active"] == made["id"] and opened["available"] is True
    assert refused.status_code == 409  # the one open
    assert elsewhere.status_code == 403
    assert deleted["items"] == [{"id": made["id"], "name": "Invented audits", "mcp": False,
                                "kind": "documents", "account": None}]
    assert workspace_demo.graph.graph_space().dropped == [DEFAULT_SCOPE]  # type: ignore[attr-defined]
    assert client.post("/v1/workspaces", json={"name": ""}).status_code == 400


def test_a_server_on_someone_s_own_neo4j_has_no_workspaces(demo: Demo) -> None:
    client = TestClient(create_app(demo), base_url=BASE)

    assert client.get("/v1/workspaces").json()["available"] is False
    assert client.post("/v1/workspaces", json={"name": "Invented"}).status_code == 409


def test_deleting_the_home_s_own_workspace_keeps_the_one_graph_db(home: Path) -> None:
    spaces = Workspaces(home)
    made = spaces.create("Invented project")
    spaces.activate(made["id"])
    (home / "neo4j-data").mkdir(parents=True)

    spaces.delete(FIRST)

    assert (home / "neo4j-data").is_dir()  # every workspace's graph is in it

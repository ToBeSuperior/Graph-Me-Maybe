"""Workspaces: graphs kept apart, one open at a time, switched to without waiting for the one
left to stop. No Neo4j: a workspace's graph is a fake space. Invented names only."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.storage import local_neo4j
from graphme.server import create_app, state
from graphme.server.jobs import BUILDING, Busy, CorpusBuild
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST, Workspaces
from tests.public.server.conftest import SETTINGS, FakeSpace, until

BASE = "http://127.0.0.1:8765"


class _Graph:
    """A workspace's graph as the server holds it, opened on a fake space."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.opened_space = self._space = FakeSpace()
        self.closed = False

    def graph_space(self) -> FakeSpace:
        return self._space

    def forget_rag(self) -> None:
        pass

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

    assert said == {"active": FIRST, "items": [{"id": FIRST, "name": "Default"}]}
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


def test_switching_opens_the_other_graph_and_lets_the_old_one_go_behind(
    workspace_demo: Demo, home: Path
) -> None:
    old = workspace_demo.graph
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.jobs["old"] = CorpusBuild(id="old", state="done")

    workspace_demo.switch_workspace(made["id"])

    assert workspace_demo.settings.local_graph == home / "workspaces" / made["id"]
    assert workspace_demo.graph is not old
    assert workspace_demo.graph.settings.local_graph == home / "workspaces" / made["id"]
    assert "old" not in workspace_demo.jobs  # the last build was the other graph's
    assert until(lambda: old.closed and stopped == [home])  # type: ignore[attr-defined]
    assert Workspaces(home).active == made["id"]


def test_a_start_opens_the_workspace_open_when_the_app_ended(home: Path) -> None:
    made = Workspaces(home).create("Invented project")
    Workspaces(home).activate(made["id"])

    again = Demo(SETTINGS.model_copy(update={"local_neo4j": home}), renderer=None,  # type: ignore[arg-type]
                 root=home / "server")

    assert again.settings.local_graph == home / "workspaces" / made["id"]


def test_no_switch_while_a_build_runs(workspace_demo: Demo) -> None:
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.build = CorpusBuild(id="b", state="reading")

    with pytest.raises(Busy):
        workspace_demo.switch_workspace(made["id"])
    assert workspace_demo.workspaces.active == FIRST  # type: ignore[union-attr]


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
    assert deleted["items"] == [{"id": made["id"], "name": "Invented audits"}]
    assert client.post("/v1/workspaces", json={"name": ""}).status_code == 400


def test_a_server_on_someone_s_own_neo4j_has_no_workspaces(demo: Demo) -> None:
    client = TestClient(create_app(demo), base_url=BASE)

    assert client.get("/v1/workspaces").json()["available"] is False
    assert client.post("/v1/workspaces", json={"name": "Invented"}).status_code == 409


def test_going_back_to_a_graph_still_stopping_waits_for_it_to_stop(
    workspace_demo: Demo, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Neo4j left keeps its graph's folder locked until it has stopped: one started over the
    # same folder before then did not start.
    import threading

    order: list[str] = []
    stopping = threading.Event()

    def slow_stop(graph: Path) -> None:
        stopping.wait(2)
        order.append(f"stopped {graph.name}")

    class _Ordered(_Graph):
        def graph_space(self) -> FakeSpace:
            order.append(f"opened {(self.settings.local_graph or home).name}")
            return self._space

    monkeypatch.setattr(local_neo4j, "stop_graph", slow_stop)
    monkeypatch.setattr(state, "GraphMe", _Ordered)
    made = workspace_demo.workspaces.create("Invented project")  # type: ignore[union-attr]
    workspace_demo.switch_workspace(made["id"])
    assert until(lambda: workspace_demo.build.state not in BUILDING)  # its passages checked
    threading.Timer(0.2, stopping.set).start()

    workspace_demo.switch_workspace(FIRST)

    assert "stopped home" in order and order.index("stopped home") < order.index("opened home")

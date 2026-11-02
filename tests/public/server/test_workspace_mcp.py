"""What an MCP client may read: switched on per workspace, off until then, and one switch
for all."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from graphme.server import create_app
from graphme.server.state import Demo
from graphme.server.workspaces import FIRST, Workspaces

from .test_workspaces import home, workspace_demo  # noqa: F401 -- fixtures


def test_a_workspace_is_closed_to_mcp_until_switched_on_and_it_is_kept(tmp_path: Path) -> None:
    spaces = Workspaces(tmp_path)
    made = spaces.create("Invented project")

    listed = spaces.listed()["items"]
    assert {i["id"]: i["mcp"] for i in listed} == {FIRST: False, made["id"]: False}

    spaces.set_mcp(made["id"], True)

    again = Workspaces(tmp_path)
    assert {i["id"]: i["mcp"] for i in again.listed()["items"]} == {FIRST: False, made["id"]: True}
    assert again.item(made["id"])["name"] == "Invented project"  # a rename keeps it too
    again.rename(made["id"], "Renamed project")
    assert again.item(made["id"])["mcp"] is True


def test_one_switch_turns_mcp_off_for_all_and_each_workspace_keeps_its_own(tmp_path: Path) -> None:
    spaces = Workspaces(tmp_path)
    spaces.set_mcp(FIRST, True)
    assert spaces.mcp_enabled is True

    spaces.set_mcp_enabled(False)

    again = Workspaces(tmp_path)
    assert again.mcp_enabled is False and again.item(FIRST)["mcp"] is True


def test_the_switches_over_http_and_from_this_machine_only(workspace_demo: Demo) -> None:  # noqa: F811
    client = TestClient(create_app(workspace_demo), base_url="http://127.0.0.1:8765")
    made = client.post("/v1/workspaces", json={"name": "Invented project"}).json()

    turned: dict[str, Any] = client.patch(f"/v1/workspaces/{made['id']}", json={"mcp": True}).json()
    assert turned == {"id": made["id"], "name": "Invented project", "mcp": True}
    listed = client.get("/v1/workspaces").json()
    assert listed["mcp_enabled"] is True
    assert {i["id"]: i["mcp"] for i in listed["items"]}[made["id"]] is True

    assert client.put("/v1/mcp", json={"enabled": False}).json()["mcp_enabled"] is False
    other = {"Origin": "https://example.com"}
    assert client.put("/v1/mcp", json={"enabled": True}, headers=other).status_code == 403
    assert client.patch(f"/v1/workspaces/{made['id']}", json={"mcp": False},
                        headers=other).status_code == 403


def test_changes_through_mcp_are_off_until_switched_on_and_kept(tmp_path: Path) -> None:
    spaces = Workspaces(tmp_path)
    assert spaces.listed()["mcp_changes"] is False

    spaces.set_mcp_changes(True)

    assert Workspaces(tmp_path).listed()["mcp_changes"] is True
    assert Workspaces(tmp_path).listed()["mcp_enabled"] is True  # the other switch kept

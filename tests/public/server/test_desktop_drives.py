"""A cloud drive's desktop client on this machine -- Box Drive, Google Drive for desktop --
found, read only once the user says to, and browsed a level at a time. Invented folders."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from graphme.adapters.sources.desktop_drives import Drive
from graphme.server.app import create_app
from graphme.server.state import Demo

BASE = "http://127.0.0.1:8765"


def _drive(tmp_path: Path) -> Path:
    root = tmp_path / "Drive"
    (root / "Specs").mkdir(parents=True)
    (root / "Specs" / "Valve Spec.docx").write_bytes(b"docx")
    (root / "Specs" / "Budget.gsheet").write_text("{}")
    (root / "Leak Test Procedure.pdf").write_bytes(b"%PDF-1.4")
    return root


@pytest.mark.parametrize("kind", ["box", "google", "onedrive"])
def test_a_drive_on_this_machine_is_found_connected_and_browsed(
    demo: Demo, tmp_path: Path, kind: str
) -> None:
    root = _drive(tmp_path)
    demo.desktop_drive = lambda name: Drive(root) if name == kind else None  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    asked = client.get(f"/v1/drives/{kind}").json()
    refused = client.get(f"/v1/drives/{kind}/folders", params={"path": "Specs"})
    connected = client.post(f"/v1/drives/{kind}/connect").json()
    specs = client.get(f"/v1/drives/{kind}/folders", params={"path": "Specs"}).json()

    # Found, but not read until the user says to; then remembered for every start.
    assert asked == {"found": True, "root": str(root), "connected": False}
    assert refused.status_code == 409
    assert connected == {"found": True, "root": str(root), "connected": True}
    assert Demo(demo.settings, renderer=None,  # type: ignore[arg-type]
                root=demo.root).drive_connected(kind)
    # A Google document is a link to the web: shown, never picked.
    assert [(i["name"], i["readable"]) for i in specs["items"]] == [
        ("Budget.gsheet", False), ("Valve Spec.docx", True)]
    assert client.delete(f"/v1/drives/{kind}").json()["connected"] is False


def test_connecting_one_drive_does_not_connect_the_other(demo: Demo, tmp_path: Path) -> None:
    root = _drive(tmp_path)
    demo.desktop_drive = lambda name: Drive(root)  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    client.post("/v1/drives/google/connect")

    assert client.get("/v1/drives/box").json()["connected"] is False


def test_without_the_client_the_page_is_told_so(demo: Demo) -> None:
    demo.desktop_drive = lambda name: None  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    assert client.get("/v1/drives/google").json() == {"found": False, "root": None,
                                                       "connected": False}
    assert client.post("/v1/drives/google/connect").status_code == 404
    assert client.get("/v1/drives/elsewhere").status_code == 400


def test_a_drive_is_connected_only_from_this_machine(demo: Demo, tmp_path: Path) -> None:
    root = _drive(tmp_path)
    demo.desktop_drive = lambda name: Drive(root)  # type: ignore[method-assign]
    client = TestClient(create_app(demo), base_url=BASE)

    elsewhere = {"origin": "https://elsewhere.invented"}
    assert client.post("/v1/drives/google/connect", headers=elsewhere).status_code == 403

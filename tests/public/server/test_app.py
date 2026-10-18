"""The server's own paths: `/health`, the contract, and the web app beside the API."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from graphme import __version__
from graphme.server import create_app
from graphme.server.state import Demo


def test_health_answers_without_the_graph_space(demo: Demo) -> None:
    def unreachable() -> None:
        raise AssertionError("health never opens the graph space")

    demo.space = unreachable  # type: ignore[assignment,method-assign]
    said = TestClient(create_app(demo)).get("/health")

    assert said.status_code == 200
    assert said.json() == {"status": "ok", "version": __version__}


def test_the_contract_names_only_the_api(demo: Demo) -> None:
    paths = set(create_app(demo).openapi()["paths"])

    assert "/health" in paths
    assert all(path == "/health" or path.startswith("/v1/") for path in paths), paths


def test_the_web_app_is_served_for_every_page_and_never_for_the_api(
    demo: Demo, tmp_path: Path
) -> None:
    """The app routes itself: any path that is not the API gets its page, a built file is
    served as it is, and an unknown API path is a 404, not the page."""
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<div id=root></div>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    (web / "favicon.svg").write_text("<svg/>")
    client = TestClient(create_app(demo, web=web))

    for path in ("/", "/manage", "/manage/docs/abc", "/qfolder"):
        got = client.get(path)
        assert got.status_code == 200 and "root" in got.text, path
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"
    for path in ("/v1", "/v1/", "/v1/nothing/at/all"):
        got = client.get(path)
        assert got.status_code == 404 and got.json()["detail"], path
    assert client.get("/health").json()["status"] == "ok"
    assert "openapi" in client.get("/docs").text
    (tmp_path / "secret.txt").write_text("outside")
    for sneaky in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/%00", "/a%00b"):
        got = client.get(sneaky)
        assert got.status_code == 200 and "root" in got.text, sneaky

    unbuilt = TestClient(create_app(demo, web=tmp_path / "absent"))
    told = unbuilt.get("/")
    assert told.status_code == 503 and "npm run build" in told.text


def test_without_a_web_app_the_root_is_the_contract(demo: Demo) -> None:
    client = TestClient(create_app(demo, web=None))

    got = client.get("/", follow_redirects=False)
    assert got.status_code in (302, 307) and got.headers["location"] == "/docs"
    assert client.get("/manage").status_code == 404


def test_routes_of_the_callers_own_sit_under_v1_behind_the_key(demo: Demo) -> None:
    """The demo's samples are a route the server does not have: put under `/v1`, asked for
    the same key as the rest, and said to the web app by `samples`."""
    from fastapi import APIRouter

    own = APIRouter()

    @own.post("/samples")
    def samples() -> dict[str, str]:
        return {"said": "invented"}

    demo.settings = demo.settings.model_copy(update={"server_key": "invented-server-key"})
    client = TestClient(create_app(demo, extra=own), base_url="http://127.0.0.1:8765")
    keyed = {"Authorization": "Bearer invented-server-key"}

    assert client.post("/v1/samples").status_code == 401
    assert client.post("/v1/samples", headers=keyed).json() == {"said": "invented"}
    assert client.get("/v1/settings", headers=keyed).json()["samples"] is True
    assert "/v1/samples" in create_app(demo, extra=own).openapi()["paths"]

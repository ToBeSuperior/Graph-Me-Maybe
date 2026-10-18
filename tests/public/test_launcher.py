"""`graphme app`: where it keeps its files, what it writes there, which port, and a second open.
Invented paths only; nothing is served."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from graphme import launcher, model_endpoint
from graphme.config import _read_dotenv, load_settings


def test_the_home_is_the_platform_s_app_folder_unless_named() -> None:
    assert launcher.home({"GRAPHME_HOME": "/x/y"}, "darwin") == Path("/x/y")
    assert launcher.home({"HOME": "/Users/a"}, "darwin") == Path(
        "/Users/a/Library/Application Support/Graph-Me-Maybe")
    assert launcher.home({"LOCALAPPDATA": r"C:\Users\a\AppData\Local"}, "win32") == Path(
        r"C:\Users\a\AppData\Local") / "Graph-Me-Maybe"
    assert launcher.home({"HOME": "/home/a"}, "linux") == Path(
        "/home/a/.local/share/graph-me-maybe")
    assert launcher.home({"XDG_DATA_HOME": "/d"}, "linux") == Path("/d/graph-me-maybe")


def test_a_settings_file_is_written_once_and_never_over_the_user_s(tmp_path: Path) -> None:
    home = tmp_path / "Application Support" / "Graph-Me-Maybe"
    launcher.prepare(home)
    written = (home / ".env").read_text()
    provider = model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER]
    assert f"GRAPHME_PROVIDER={model_endpoint.DEFAULT_PROVIDER}" in written
    assert f"{provider.key_env}=" in written
    (home / ".env").write_text("OPENAI_API_KEY=invented\n")
    launcher.prepare(home)
    assert (home / ".env").read_text() == "OPENAI_API_KEY=invented\n"


def test_the_installed_viewer_is_used_unless_another_is_named(tmp_path: Path) -> None:
    (tmp_path / "viewer").mkdir()
    assert launcher.asset_defaults(tmp_path, {}) == {
        "GRAPHME_VIEWER_DIR": str(tmp_path / "viewer")}
    assert launcher.asset_defaults(tmp_path, {"GRAPHME_VIEWER_DIR": "/mine"}) == {}
    (tmp_path / ".env").write_text("GRAPHME_VIEWER_DIR=/in-env\n")
    assert launcher.asset_defaults(tmp_path, {}) == {}
    assert launcher.asset_defaults(tmp_path / "none", {}) == {}


def test_the_installed_web_app_is_served(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<!doctype html>")
    assert launcher.web_directory(tmp_path) == tmp_path / "web"


def test_a_taken_port_is_passed_over() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert launcher.free_port(port) != port


def test_opened_again_it_only_shows_the_running_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    served: list[int] = []
    monkeypatch.setattr(launcher, "running", lambda port: port == 8765)
    launcher.launch(port=None, browser=True, environ={"GRAPHME_HOME": str(tmp_path)},
                    open_url=opened.append,
                    serve=lambda port, web, on_ready=None: served.append(port))
    assert opened == ["http://127.0.0.1:8765"] and served == []


def test_started_it_serves_from_its_home_and_opens_when_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher, "running", lambda port: False)
    monkeypatch.chdir(tmp_path)

    def serve(port: int, web: Path | None, on_ready=None) -> None:
        assert Path.cwd() == tmp_path / "home"
        on_ready()

    launcher.launch(port=8799, browser=True, environ={"GRAPHME_HOME": str(tmp_path / "home")},
                    open_url=opened.append, serve=serve)
    assert opened == ["http://127.0.0.1:8799"]
    assert (tmp_path / "home" / ".env").is_file()


def test_the_settings_file_written_on_a_fresh_home_loads(tmp_path: Path) -> None:
    launcher.prepare(tmp_path)
    written = _read_dotenv(tmp_path / ".env")
    known = {p.key_env for p in model_endpoint.PROVIDERS.values()} | {
        e.key_env for e in model_endpoint.EMBEDDERS.values()}
    assert {name for name in written if name.endswith("_KEY")} <= known
    assert load_settings(written).provider == model_endpoint.DEFAULT_PROVIDER


def test_under_wsl_the_page_opens_in_the_windows_browser() -> None:
    # The Linux Chrome there is the renderer's; a window of it is not where anyone looks.
    ran: list[list[str]] = []
    fallback: list[str] = []

    launcher.open_in_browser(
        "http://127.0.0.1:8765", environ={"WSL_DISTRO_NAME": "Ubuntu"},
        which=lambda name: f"/usr/bin/{name}" if name == "wslview" else None,
        run=lambda args, **_: ran.append(args), fallback=fallback.append)

    assert ran == [["/usr/bin/wslview", "http://127.0.0.1:8765"]]
    assert fallback == []


def test_under_wsl_without_wslview_explorer_opens_it() -> None:
    ran: list[list[str]] = []

    launcher.open_in_browser(
        "http://127.0.0.1:8765", environ={"WSL_DISTRO_NAME": "Ubuntu"},
        which=lambda name: None, run=lambda args, **_: ran.append(args),
        fallback=lambda url: None)

    assert ran == [["explorer.exe", "http://127.0.0.1:8765"]]


def test_a_browser_named_by_the_user_is_kept_even_under_wsl() -> None:
    fallback: list[str] = []
    ran: list[object] = []

    launcher.open_in_browser(
        "http://127.0.0.1:8765", environ={"WSL_DISTRO_NAME": "Ubuntu", "BROWSER": "firefox"},
        which=lambda name: "/usr/bin/x", run=lambda args, **_: ran.append(args),
        fallback=fallback.append)

    assert (ran, fallback) == ([], ["http://127.0.0.1:8765"])


@pytest.mark.skipif(sys.platform == "win32", reason="TIME_WAIT reuse is the POSIX rule")
def test_a_port_the_app_just_closed_is_free_again() -> None:
    # A server that closed a connection first leaves it in TIME_WAIT for a minute. The server
    # binds over it (SO_REUSEADDR); the probe did not, and the app came up on the next port.
    with socket.socket() as server:
        # As uvicorn binds; Linux reuses a TIME_WAIT port only when both sides asked to.
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _ = server.accept()
        accepted.close()  # the server's side closes first: its end waits
        client.close()

    assert launcher.free_port(port) == port


def test_a_port_something_listens_on_is_skipped() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]

        assert launcher.free_port(port) != port

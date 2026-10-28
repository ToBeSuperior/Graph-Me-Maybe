"""`graphme app`: where it keeps its files, what it writes there, which port, and a second open.
Invented paths only; nothing is served."""
from __future__ import annotations

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from graphme import launcher, model_endpoint, processes
from graphme.config import load_settings, read_dotenv


@pytest.fixture(autouse=True)
def _signals_as_they_were() -> Iterator[None]:
    """`main` blocks the ending signals in the thread that runs it -- here pytest's own: put
    back after each test, or every child a later test starts inherits them blocked."""
    import signal

    if not hasattr(signal, "pthread_sigmask"):
        yield
        return
    before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    yield
    signal.pthread_sigmask(signal.SIG_SETMASK, before)

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
    monkeypatch.setattr(launcher, "running", lambda port, home: port == 8765)
    launcher.launch(port=None, browser=True, environ={"GRAPHME_HOME": str(tmp_path)},
                    open_url=opened.append,
                    serve=lambda port, web, on_ready=None: served.append(port))
    assert opened == ["http://127.0.0.1:8765"] and served == []


def test_started_it_serves_from_its_home_and_opens_when_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher, "running", lambda port, home: False)
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
    written = read_dotenv(tmp_path / ".env")
    known = {p.key_env for p in model_endpoint.PROVIDERS.values()} | {
        e.key_env for e in model_endpoint.EMBEDDERS.values() if e.key_env}
    assert {name for name in written if name.endswith("_KEY")} <= known
    assert load_settings(written).provider == model_endpoint.DEFAULT_PROVIDER


def test_under_wsl_the_page_opens_in_the_windows_browser() -> None:
    # The Linux Chrome there is the renderer's; a window of it is not where anyone looks.
    ran: list[list[str]] = []
    fallback: list[str] = []

    processes.open_in_browser(
        "http://127.0.0.1:8765", environ={"WSL_DISTRO_NAME": "Ubuntu"},
        which=lambda name: f"/usr/bin/{name}" if name == "wslview" else None,
        run=lambda args, **_: ran.append(args), fallback=fallback.append)

    assert ran == [["/usr/bin/wslview", "http://127.0.0.1:8765"]]
    assert fallback == []


def test_under_wsl_without_wslview_explorer_opens_it() -> None:
    ran: list[list[str]] = []

    processes.open_in_browser(
        "http://127.0.0.1:8765", environ={"WSL_DISTRO_NAME": "Ubuntu"},
        which=lambda name: None, run=lambda args, **_: ran.append(args),
        fallback=lambda url: None)

    assert ran == [["explorer.exe", "http://127.0.0.1:8765"]]


def test_a_browser_named_by_the_user_is_kept_even_under_wsl() -> None:
    fallback: list[str] = []
    ran: list[object] = []

    processes.open_in_browser(
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


@pytest.mark.parametrize(("said", "ours"), [
    ({"status": "ok", "home": "/elsewhere/graph-me-maybe"}, False),  # WSL's app, seen from Windows
    ({"status": "ok"}, True),  # an app from before the home was said
])
def test_an_app_answering_from_another_home_is_not_this_one(
    tmp_path: Path, said: dict[str, str], ours: bool
) -> None:
    class Answer:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return said

    assert launcher.running(8765, tmp_path, get=lambda url, timeout: Answer()) is ours
    mine = {**said, "home": str(tmp_path)}
    assert launcher.running(8765, tmp_path, get=lambda url, timeout: type(
        "A", (), {"status_code": 200, "json": staticmethod(lambda: mine)})()) is True


def test_in_a_window_the_server_runs_behind_it_and_goes_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    monkeypatch.setattr(launcher, "running", lambda port, home: False)
    monkeypatch.chdir(tmp_path)
    stopped = threading.Event()
    shown: list[str] = []

    def serve(port: int, web: Path | None, on_ready=None) -> None:
        on_ready()
        stopped.wait(5)  # serves until the process ends

    def window(url: str) -> bool:
        shown.append(url)
        return True

    launcher.launch(port=8799, browser=True, environ={"GRAPHME_HOME": str(tmp_path / "home")},
                    open_url=lambda url: pytest.fail("no browser when a window shows it"),
                    serve=serve, window=window)
    stopped.set()
    assert shown == ["http://127.0.0.1:8799"]


def test_without_a_window_the_browser_shows_it_and_the_server_keeps_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "running", lambda port, home: False)
    monkeypatch.chdir(tmp_path)
    opened: list[str] = []
    served: list[str] = []

    def serve(port: int, web: Path | None, on_ready=None) -> None:
        on_ready()
        served.append("until stopped")

    launcher.launch(port=8799, browser=True, environ={"GRAPHME_HOME": str(tmp_path / "home")},
                    open_url=opened.append, serve=serve, window=lambda url: False)
    assert opened == ["http://127.0.0.1:8799"] and served == ["until stopped"]


def test_a_server_that_never_comes_up_does_not_leave_the_window_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "running", lambda port, home: False)
    monkeypatch.chdir(tmp_path)

    def serve(port: int, web: Path | None, on_ready=None) -> None:
        raise OSError("address in use")

    with pytest.raises(SystemExit, match="app.log"):
        launcher.launch(port=8799, browser=True,
                        environ={"GRAPHME_HOME": str(tmp_path / "home")},
                        open_url=lambda url: None, serve=serve, window=lambda url: True)


def test_the_window_app_is_a_program_without_a_console() -> None:
    import tomllib

    project = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert project["project"]["gui-scripts"]["graphme-app"] == "graphme.launcher:main"
    pywebview = [d for d in project["project"]["dependencies"] if d.startswith("pywebview")]
    assert pywebview and "sys_platform" in pywebview[0]  # Linux and WSL keep the browser


@pytest.mark.parametrize("argument", ["app", "elsewhere"])
def test_the_window_app_takes_only_a_full_path_for_its_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argument: str
) -> None:
    # A shortcut made for `graphme.exe app` kept its `app` when the installer pointed it at
    # graphme-app.exe, which then took `app` for a home under its working folder: no web app,
    # no keys, and one folder deeper each time it opened.
    seen: list[str] = []
    exits: list[int] = []
    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(launcher, "launch",
                        lambda **kw: seen.append(kw["environ"]["GRAPHME_HOME"]))
    monkeypatch.setattr(launcher.os, "_exit", exits.append)
    monkeypatch.setattr(launcher.sys, "argv", ["graphme-app", argument])
    launcher.main()
    monkeypatch.setattr(launcher.sys, "argv", ["graphme-app", str(tmp_path / "custom")])
    launcher.main()
    assert seen == [str(tmp_path / "home"), str(tmp_path / "custom")]
    # The server reads its settings, and its Neo4j's home, from this process's environment:
    # given only to the launcher, the home was the default one and Neo4j was fetched there.
    assert os.environ["GRAPHME_HOME"] == str(tmp_path / "custom")
    # Closed, the process ends, whatever threads are left: the window is the app.
    assert exits == [0, 0]


def test_closed_it_stops_the_neo4j_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without it the window app's Neo4j outlived it: os._exit skips what atexit would do.
    from graphme.adapters.storage import local_neo4j

    stopped: list[bool] = []
    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path))
    monkeypatch.setattr(launcher, "launch", lambda **kw: None)
    monkeypatch.setattr(launcher.os, "_exit", lambda code: None)
    monkeypatch.setattr(launcher.sys, "argv", ["graphme-app"])
    monkeypatch.setattr(local_neo4j, "stop_started", lambda: stopped.append(True))
    launcher.main()
    assert stopped == [True]


def test_ended_by_a_signal_it_still_stops_the_neo4j_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Quit from the Dock or ended by the system, the window never returned: the app's Neo4j,
    # a process group of its own, went on running with no app. A Python handler never ran --
    # the main thread sits in the window's loop -- and the app ignored the signal altogether;
    # the ending is waited for on a thread of its own, the signal blocked everywhere else.
    import signal
    import threading

    from graphme.adapters.storage import local_neo4j

    if not hasattr(signal, "sigwait"):
        pytest.skip("no POSIX signals here")
    blocked: list[set[int]] = []
    stopped = threading.Event()
    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path))
    monkeypatch.setattr(launcher, "launch", lambda **kw: None)
    monkeypatch.setattr(launcher.os, "_exit", lambda code: None)
    monkeypatch.setattr(launcher.sys, "argv", ["graphme-app"])
    monkeypatch.setattr(launcher.signal, "pthread_sigmask",
                        lambda how, sigs: blocked.append(set(sigs)) or set())
    monkeypatch.setattr(launcher.signal, "sigwait", lambda sigs: signal.SIGTERM)
    monkeypatch.setattr(local_neo4j, "stop_started", stopped.set)
    launcher.main()

    assert stopped.wait(5)
    assert blocked and signal.SIGTERM in blocked[0]


def test_a_window_closed_by_quitting_the_app_stops_the_neo4j_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Quitting the app (Cmd+Q) ends the process from inside the window's own loop: the window
    # is closed, and what follows `webview.start` never runs.
    import types

    from graphme.adapters.storage import local_neo4j

    stopped: list[bool] = []
    closed: list[object] = []

    class Events:
        def __init__(self) -> None:
            self.closed = self

        def __iadd__(self, handler: object) -> Events:
            closed.append(handler)
            return self

    window = types.SimpleNamespace(events=Events())
    fake = types.SimpleNamespace(settings={}, create_window=lambda *a, **k: window,
                                 start=lambda **k: None)
    monkeypatch.setitem(launcher.sys.modules, "webview", fake)
    monkeypatch.setattr(local_neo4j, "stop_started", lambda: stopped.append(True))

    assert launcher.show_window("http://127.0.0.1:1", tmp_path)
    closed[0]()  # type: ignore[operator]
    assert stopped == [True]

"""`graphme app` and `graphme-app`: the installed app -- its folder, its settings file, the
assets the installer put there, a free port, and the page opened once the server answers: in
a window of its own (`graphme-app`, the shortcut's) or the browser."""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from graphme import model_endpoint, processes, window_chrome
from graphme.config import app_home as home

FIRST_PORT = 8765
# This tree's own providers: the default's key first, then the others' and the embedders'.
_KEYS = dict.fromkeys(
    [model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER].key_env]
    + [p.key_env for p in model_endpoint.PROVIDERS.values()]
    + [e.key_env for e in model_endpoint.EMBEDDERS.values() if e.key_env])
ENV_TEMPLATE = (
    "# Graph-Me-Maybe settings. Keys are set from the app (Manage > Keys and model) or here.\n"
    f"GRAPHME_PROVIDER={model_endpoint.DEFAULT_PROVIDER}\n"
    + "".join(f"{name}=\n" for name in _KEYS))


def prepare(home: Path) -> None:
    """The folder, and a settings file in it unless there is one: an update keeps the keys."""
    home.mkdir(parents=True, exist_ok=True)
    env = home / ".env"
    if not env.exists():
        env.write_text(ENV_TEMPLATE, encoding="utf-8")
        env.chmod(0o600)


def web_directory(home: Path) -> Path | None:
    """The installed web app, else a checkout's build (the server's own default)."""
    if (home / "web" / "index.html").is_file():
        return home / "web"
    return None


def free_port(start: int = FIRST_PORT) -> int:
    for port in range(start, start + 50):
        with socket.socket() as probe:
            if sys.platform != "win32":
                # As the server binds: a connection the last run closed, still in TIME_WAIT,
                # is not a port in use. (On Windows the option would let a bind steal one.)
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port from {start}")


def running(port: int, home: Path, *, get: Callable[..., Any] = httpx.get) -> bool:
    """This home's Graph-Me-Maybe already answering there. One from before the home was said
    is taken for it."""
    try:
        answer = get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        said = answer.json()
        if answer.status_code != 200 or said.get("status") != "ok":
            return False
    except (httpx.HTTPError, ValueError):
        return False
    try:
        return "home" not in said or os.path.samefile(said["home"], home)
    except OSError:  # a folder of another system, or none
        return False


def show_window(url: str, storage: Path,
                on_closing: Callable[[Any], None] | None = None) -> bool:
    """The page in a window of its own -- Edge WebView2 on Windows, WebKit on macOS -- until it
    is closed; `on_closing` is handed the window as it closes. False when there is none to show
    it in (Linux and WSL install no webview)."""
    try:
        import webview  # type: ignore[import-not-found,unused-ignore]  # not on Linux
    except ImportError:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True  # an export's .dump and bundle
        # On Windows the header is the title bar (`window_chrome`): Windows paints its own light
        # over the dark app whenever Windows is light.
        chrome = window_chrome.ChromeApi() if window_chrome.wanted() else None
        if chrome is not None:
            try:
                window_chrome.install(chrome)
            except Exception:  # noqa: BLE001 -- Windows' own title bar, then
                logging.getLogger(__name__).exception("the window keeps its own title bar")
                chrome = None
        # With its own title bar the window has no frame (`window_chrome`); pywebview's script
        # that drags a frameless window from anywhere on the page is not wanted -- the header's
        # drag regions move it.
        window = webview.create_window("Graph-Me-Maybe", url, width=1280, height=860,
                                       min_size=(900, 600), text_select=True, js_api=chrome,
                                       frameless=chrome is not None, easy_drag=False)
        if chrome is not None and window is not None:
            chrome.attach(window)
        # Quitting the app (Cmd+Q, the Dock) ends the process from inside the window's loop:
        # what follows `start` never runs, and the Neo4j it started -- a process group of its
        # own -- would outlive it.
        if window is not None:
            window.events.closed += stop_started
            if on_closing is not None:
                window.events.closing += lambda: on_closing(window)
        # Kept between runs, as a browser keeps them: the theme, the language, an API key.
        webview.start(private_mode=False, storage_path=str(storage))
    except Exception:  # noqa: BLE001 -- no WebView2 runtime, no GUI session
        logging.getLogger(__name__).exception("no app window; opening the browser instead")
        return False
    return True


def stop_started() -> None:
    """The Neo4j and the search model servers this process started, stopped: `os._exit` runs
    no exit handler."""
    from graphme import runtime
    from graphme.adapters.storage import local_neo4j

    local_neo4j.stop_started()
    runtime.stop_local_models()


def _stop_on_endings(also: Callable[[], None] = lambda: None) -> None:
    """Ended by the system or `kill`, the Neo4j goes too, then the app. The main thread sits in
    the window's loop, where a Python signal handler never runs -- one installed there left
    the app ignoring the signal -- so the ending is blocked in every thread started after this
    and waited for on one of its own."""
    if not hasattr(signal, "sigwait"):
        return  # no POSIX signals: Windows ends a process outright
    endings = {signal.SIGTERM, signal.SIGHUP}
    signal.pthread_sigmask(signal.SIG_BLOCK, endings)

    def wait() -> None:
        signal.sigwait(endings)
        stop_started()
        also()
        os._exit(0)

    threading.Thread(target=wait, name="endings", daemon=True).start()


def launch(
    *, port: int | None, browser: bool, environ: Mapping[str, str] = os.environ,
    open_url: Callable[[str], object] = processes.open_in_browser,
    serve: Callable[..., None] | None = None,
    window: Callable[[str], bool] | None = None,
) -> None:
    """Open the app: the running one if there is, else a server started from its home. With a
    `window`, the server runs behind it and ends when it is closed."""
    where = home(environ)
    if port is None and running(FIRST_PORT, where):
        url = f"http://127.0.0.1:{FIRST_PORT}"
        if (window is None or not window(url)) and browser:
            open_url(url)
        return
    prepare(where)
    os.chdir(where)  # settings and data are read relative to the working directory
    chosen = port or free_port()
    serve_demo: Callable[..., None]
    if serve is None:
        from graphme.server.demo import run
        serve_demo = run
    else:
        serve_demo = serve
    url = f"http://127.0.0.1:{chosen}"
    print(f"\n  Graph-Me-Maybe · {url}\n")

    def show() -> None:
        open_url(url)

    if window is None:
        serve_demo(chosen, web_directory(where), on_ready=show if browser else None)
        return
    ready = threading.Event()

    def serving() -> None:
        try:
            serve_demo(chosen, web_directory(where), on_ready=ready.set)
        except Exception:  # noqa: BLE001 -- said in the log; the wait below gives up
            logging.getLogger(__name__).exception("the server stopped")

    server = threading.Thread(target=serving, daemon=True)
    server.start()
    while not ready.wait(0.2):
        if not server.is_alive():
            raise SystemExit(f"Graph-Me-Maybe did not start -- see {where / 'app.log'}")
    if not window(url):
        open_url(url)
        server.join()


def main() -> None:
    """`graphme-app`: what the shortcut starts, with no console -- the window, on the agent
    (`agent.py`), which it starts when none runs; `--agent` is the agent itself. A Windows
    shortcut cannot set GRAPHME_HOME, so a home other than the default comes as an argument -- a
    full path only: a shortcut made for `graphme.exe app` still says `app`."""
    # The webview finds its own files from the program's path, which the move into the home
    # below would leave pointing nowhere when it was given relative.
    sys.argv[0] = os.path.abspath(sys.argv[0])
    given = sys.argv[1:]
    for named in given:
        if Path(named).is_absolute():
            # Into this process's own environment: the server reads its settings, and its
            # Neo4j's home, from there -- not from what the launcher alone is handed.
            os.environ["GRAPHME_HOME"] = named
    environ = dict(os.environ)
    where = home(environ)
    if sys.stdout is None or sys.stderr is None:  # started without a console: say it in a file
        prepare(where)
        log = open(where / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = log
    from graphme import agent, agent_file

    if "--agent" in given:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        _stop_on_endings(also=lambda: agent_file.remove_server_file(where))
        agent.run_agent(where)
    else:
        _stop_on_endings()
        agent.open_window(where)
    # Ended: whatever threads a build or a driver left, the process goes.
    os._exit(0)


if __name__ == "__main__":
    main()

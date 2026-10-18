"""`graphme app` and `graphme-app`: the installed app -- its folder, its settings file, the
assets the installer put there, a free port, and the page opened once the server answers: in
a window of its own (`graphme-app`, the shortcut's) or the browser."""
from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import threading
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from graphme import model_endpoint, processes
from graphme.config import app_home as home

FIRST_PORT = 8765
# This tree's own providers: the default's key first, then the others' and the embedders'.
_KEYS = dict.fromkeys(
    [model_endpoint.PROVIDERS[model_endpoint.DEFAULT_PROVIDER].key_env]
    + [p.key_env for p in model_endpoint.PROVIDERS.values()]
    + [e.key_env for e in model_endpoint.EMBEDDERS.values()])
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


def open_in_browser(
    url: str, *, environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., object] = processes.run,
    fallback: Callable[[str], object] = webbrowser.open,
) -> None:
    """The page in the user's browser. Under WSL that is Windows' default one: Python picks
    the Linux Chrome there first, which is the renderer's, in a window of its own. A browser
    the user names in BROWSER is always kept."""
    if ("WSL_DISTRO_NAME" in environ or "WSL_INTEROP" in environ) and "BROWSER" not in environ:
        opener = which("wslview")
        run([opener, url] if opener else ["explorer.exe", url], check=False)
        return
    fallback(url)


def show_window(url: str, storage: Path) -> bool:
    """The page in a window of its own -- Edge WebView2 on Windows, WebKit on macOS -- until it
    is closed. False when there is none to show it in (Linux and WSL install no webview)."""
    try:
        import webview  # type: ignore[import-not-found,unused-ignore]  # not on Linux
    except ImportError:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True  # an export's .dump and bundle
        webview.create_window("Graph-Me-Maybe", url, width=1280, height=860,
                              min_size=(900, 600), text_select=True)
        # Kept between runs, as a browser keeps them: the theme, the language, an API key.
        webview.start(private_mode=False, storage_path=str(storage))
    except Exception:  # noqa: BLE001 -- no WebView2 runtime, no GUI session
        logging.getLogger(__name__).exception("no app window; opening the browser instead")
        return False
    return True


def launch(
    *, port: int | None, browser: bool, environ: Mapping[str, str] = os.environ,
    open_url: Callable[[str], object] = open_in_browser,
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
    """`graphme-app`: what the shortcut starts, with no console. A Windows shortcut cannot set
    GRAPHME_HOME, so a home other than the default comes as the one argument -- a full path
    only: a shortcut made for `graphme.exe app` still says `app`."""
    # The webview finds its own files from the program's path, which the move into the home
    # below would leave pointing nowhere when it was given relative.
    sys.argv[0] = os.path.abspath(sys.argv[0])
    environ = dict(os.environ)
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_absolute():
        environ["GRAPHME_HOME"] = sys.argv[1]
    where = home(environ)
    if sys.stdout is None or sys.stderr is None:  # started without a console: say it in a file
        prepare(where)
        log = open(where / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = log
    launch(port=None, browser=True, environ=environ,
           window=lambda url: show_window(url, where / "webview"))
    # The window is closed: the app ends with it, whatever threads a build or a driver left.
    os._exit(0)

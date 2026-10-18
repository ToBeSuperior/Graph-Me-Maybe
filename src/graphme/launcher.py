"""`graphme app`: the installed app -- its folder, its settings file, the assets the installer
put there, a free port, and the browser opened once the server answers."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx

from graphme import model_endpoint
from graphme.config import _read_dotenv

NAME = "Graph-Me-Maybe"
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


def home(environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """Where the app keeps its settings, its graph data and the installed assets."""
    if named := environ.get("GRAPHME_HOME"):
        return Path(named)
    if platform == "win32":
        return Path(environ["LOCALAPPDATA"]) / NAME
    if platform == "darwin":
        return Path(environ["HOME"]) / "Library" / "Application Support" / NAME
    data = environ.get("XDG_DATA_HOME") or str(Path(environ["HOME"]) / ".local" / "share")
    return Path(data) / "graph-me-maybe"


def prepare(home: Path) -> None:
    """The folder, and a settings file in it unless there is one: an update keeps the keys."""
    home.mkdir(parents=True, exist_ok=True)
    env = home / ".env"
    if not env.exists():
        env.write_text(ENV_TEMPLATE, encoding="utf-8")
        env.chmod(0o600)


def asset_defaults(home: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """The viewer the installer put in `home`, unless the environment or `.env` names one."""
    viewer = home / "viewer"
    named = "GRAPHME_VIEWER_DIR" in environ or "GRAPHME_VIEWER_DIR" in _read_dotenv(home / ".env")
    return {} if named or not viewer.is_dir() else {"GRAPHME_VIEWER_DIR": str(viewer)}


def web_directory(home: Path) -> Path | None:
    """The installed web app, else a checkout's build (the server's own default)."""
    if (home / "web" / "index.html").is_file():
        return home / "web"
    return None


def free_port(start: int = FIRST_PORT) -> int:
    for port in range(start, start + 50):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port from {start}")


def running(port: int) -> bool:
    """A Graph-Me-Maybe server already answering there."""
    try:
        answer = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        return answer.status_code == 200 and answer.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def open_in_browser(
    url: str, *, environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., object] = subprocess.run,
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


def launch(
    *, port: int | None, browser: bool, environ: Mapping[str, str] = os.environ,
    open_url: Callable[[str], object] = open_in_browser,
    serve: Callable[..., None] | None = None,
) -> None:
    """Open the app: the running one if there is, else a server started from its home."""
    if port is None and running(FIRST_PORT):
        if browser:
            open_url(f"http://127.0.0.1:{FIRST_PORT}")
        return
    where = home(environ)
    prepare(where)
    os.chdir(where)  # settings and data are read relative to the working directory
    for name, value in asset_defaults(where, environ).items():
        os.environ.setdefault(name, value)
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

    serve_demo(chosen, web_directory(where), on_ready=show if browser else None)

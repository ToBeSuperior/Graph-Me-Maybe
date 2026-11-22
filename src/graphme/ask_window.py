"""The small window the right-click "Ask" opens (`graphme ask-window`): a conversation about the
files picked, kept above other windows. It is a process of its own, as the app's window is
(`agent.py`), on the agent -- started first when none runs.

The window's address names the conversation only (`/mini/<id>`); the picked paths reach its page
through the window itself (`MiniApi.paths`), so a path is never put in an address. The page
builds the files no graph holds, showing how far, and offers those another workspace holds to
be asked there (`useScopeDrop`)."""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from graphme import agent, agent_file, processes
from graphme.agent_file import pid_alive, read_server_file

PLACE = "mini-window.json"
"""Where the window was last left and how see-through, in the app home: the page's storage is no
place for it."""
SIZE = {"width": 420, "height": 560}
MIN_SIZE = (320, 400)
OPACITY = (0.4, 1.0)
"""How see-through the window may be: never so far it cannot be found again."""
_THREAD_ID = re.compile(r"[0-9A-Za-z_-]+")


def asked(client: httpx.Client, paths: list[str]) -> str:
    """The conversation begun about `paths`; its id. It asks the workspace holding most of the
    files -- a mail workspace too, which is only read -- else the one a missing file may be
    built into (never a mail workspace), else the open one; about the documents found, or
    about none until the page has built the rest."""
    scope = client.post("/v1/scope", json={"paths": paths}).raise_for_status().json()
    body: dict[str, Any] = {"scope": scope["document_ids"]}
    if where := scope.get("workspace") or scope.get("build_into"):
        body["workspace"] = where
    return str(client.post("/v1/threads", json=body).raise_for_status().json()["id"])


def _kept(home: Path) -> dict[str, Any]:
    try:
        kept = json.loads((home / PLACE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return kept if isinstance(kept, dict) else {}


def _keep(home: Path, **changed: Any) -> None:
    (home / PLACE).write_text(json.dumps({**_kept(home), **changed}), encoding="utf-8")


def clamped(value: float) -> float:
    return min(max(float(value), OPACITY[0]), OPACITY[1])


def kept_opacity(home: Path) -> float:
    try:
        return clamped(_kept(home).get("opacity", OPACITY[1]))
    except (TypeError, ValueError):
        return OPACITY[1]


def _on_main(native: Any, work: Callable[[], None]) -> None:
    """`work` on the window's own thread, where its backend lets it be changed."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper  # type: ignore[import-not-found,unused-ignore]

        AppHelper.callAfter(work)
    elif sys.platform == "win32":  # pragma: no cover -- Windows only
        from System import Action  # type: ignore[import-not-found,unused-ignore]

        native.Invoke(Action(work))
    else:
        work()


def apply_opacity(native: Any, value: float, *,
                  on_main: Callable[[Any, Callable[[], None]], None] = _on_main) -> None:
    """The whole window `value` opaque: the NSWindow's alpha on macOS, the form's opacity on
    Windows; any other backend has none, and is left as it is."""
    if hasattr(native, "setAlphaValue_"):
        on_main(native, lambda: native.setAlphaValue_(value))
    elif hasattr(native, "Opacity"):
        on_main(native, lambda: setattr(native, "Opacity", value))


def restore_opacity(home: Path, native: Any, *,
                    on_main: Callable[[Any, Callable[[], None]], None] = _on_main) -> None:
    if (value := kept_opacity(home)) < OPACITY[1]:
        apply_opacity(native, value, on_main=on_main)


def place(home: Path) -> dict[str, int]:
    """Where the window opens: where it was left, else its size alone (the system places it)."""
    kept = _kept(home)
    try:
        return {name: int(kept[name]) for name in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return dict(SIZE)


def placed(home: Path, screens: list[Any]) -> dict[str, int]:
    """`place`, its position dropped when it is on none of `screens` (a screen since
    unplugged) or the screens are not known: the system places the window then."""
    at = place(home)
    if "x" in at and not any(s.x <= at["x"] < s.x + s.width and s.y <= at["y"] < s.y + s.height
                             for s in screens):
        return {"width": at["width"], "height": at["height"]}
    return at


def keep_place(home: Path, window: Any) -> None:
    _keep(home, **{name: int(getattr(window, name)) for name in ("x", "y", "width", "height")})


def open_main(home: Path, thread_id: str, *,
              front: Callable[[int], bool] = agent.front,
              start: Callable[[Path], None] = agent._open_app) -> None:
    """Conversation `thread_id` in the app's window: the open one sent there and brought
    forward, else one opened at it."""
    if not _THREAD_ID.fullmatch(thread_id):
        return
    agent.ask_goto(home, f"/c/{thread_id}")
    said = read_server_file(home)
    if said and said.window_pid and pid_alive(said.window_pid) and front(said.window_pid):
        return
    start(home)


class MiniApi:
    """What the window lets its own page ask of it (pywebview's `js_api`). The pin is the
    window's own: no other process, nor the server, knows of this window."""

    def __init__(self, home: Path, paths: list[str], *,
                 on_main: Callable[[Any, Callable[[], None]], None] = _on_main) -> None:
        self._home, self._paths, self._on_main = home, paths, on_main
        self._window: Any = None

    def paths(self) -> list[str]:
        """The files and folders picked."""
        return list(self._paths)

    def pin(self, on: bool) -> None:
        """The window kept above the others, or not."""
        self._window.on_top = bool(on)

    def set_opacity(self, value: float) -> None:
        """The whole window this opaque, within `OPACITY`; kept for the next time it opens."""
        value = clamped(value)
        apply_opacity(self._window.native, value, on_main=self._on_main)
        _keep(self._home, opacity=value)

    def opacity(self) -> float:
        """How opaque the window is now, for the page's slider."""
        return kept_opacity(self._home)

    def open_main(self, thread_id: str) -> None:
        open_main(self._home, thread_id)


def show_mini(url: str, home: Path, paths: list[str]) -> bool:
    """`url` in the small window until it is closed; False when there is no webview here."""
    try:
        import webview  # type: ignore[import-not-found,unused-ignore]  # not on Linux
    except ImportError:
        return False
    page = MiniApi(home, paths)
    try:
        screens = list(webview.screens)
    except Exception:  # noqa: BLE001 -- not known: the system places the window
        screens = []
    at = placed(home, screens)
    window = webview.create_window("Graph-Me-Maybe", url, width=at["width"], height=at["height"],
                                   x=at.get("x"), y=at.get("y"), min_size=MIN_SIZE,
                                   resizable=True, on_top=True, text_select=True, js_api=page)
    if window is None:
        return False
    page._window = window
    window.events.shown += lambda: restore_opacity(home, window.native)
    window.events.closing += lambda: keep_place(home, window)
    # The app window's storage: the same theme and language.
    webview.start(private_mode=False, storage_path=str(home / "webview"))
    return True


def main(paths: list[str], home: Path, *,
         connect: Callable[[Path], httpx.Client] = agent_file.connect,
         show: Callable[[str, Path, list[str]], bool] = show_mini,
         browse: Callable[[str], None] = processes.open_in_browser) -> None:
    """The small window on a conversation about `paths`."""
    whole = [str(Path(p).expanduser().resolve()) for p in paths]
    with connect(home) as client:
        thread = asked(client, whole)
        url = f"{str(client.base_url).rstrip('/')}/mini/{thread}"
    if not show(url, home, whole):
        browse(url)  # no webview here (Linux): the browser


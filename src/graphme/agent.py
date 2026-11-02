"""The app in the background, and its window as a process of its own.

The agent runs the server, its Neo4j and the search model behind an icon -- in the menu bar on
macOS (no Dock icon), the notification area on Windows -- and keeps running until quit from that
icon's menu, so an MCP client reaches the graph with no window open. The window only shows the
agent's address: closing it ends the window alone. They are two processes because on macOS the
icon and the webview both want the main thread. The agent is found by `server.json`
(`agent_file`).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from graphme import agent_file, login_item, processes
from graphme.agent_file import pid_alive, read_server_file

NOTICES = "notices.json"
STATE_EVERY_SECONDS = 3.0
APP_BUNDLE = Path.home() / "Applications" / "Graph-Me-Maybe.app"
BACKGROUND_NOTICE = (
    "Graph-Me-Maybe keeps running in the background, so an MCP client can still reach your "
    "documents. Quit it completely from its icon in the menu bar (macOS) or the notification "
    "area (Windows).")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentState:
    workspace: str | None
    """The workspace open in the app, by name."""
    mcp_enabled: bool
    login: bool


@dataclass(frozen=True)
class MenuEntry:
    text: str
    action: str | None
    """open | mcp | login | quit; None for a line that only says something."""
    checked: bool = False


def agent_menu(state: AgentState) -> list[MenuEntry]:
    """The icon's menu for `state`."""
    said = " · ".join(["Running", *([state.workspace] if state.workspace else []),
                       "MCP on" if state.mcp_enabled else "MCP off"])
    return [MenuEntry(said, None),
            MenuEntry("Open the app", "open"),
            MenuEntry("MCP on", "mcp", state.mcp_enabled),
            MenuEntry("Start at login", "login", state.login),
            MenuEntry("Quit completely", "quit")]


def _python() -> str:
    """This Python; on Windows its windowless twin, so a start at login opens no console."""
    quiet = Path(sys.executable).with_name("pythonw.exe")
    return str(quiet) if sys.platform == "win32" and quiet.is_file() else sys.executable


def agent_command(home: Path) -> list[str]:
    return [_python(), "-m", "graphme.launcher", "--agent", str(home)]


def _end_window(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows


def _stop_started() -> None:
    from graphme.launcher import stop_started

    stop_started()


def quit_completely(
    home: Path, *, kill: Callable[[int], None] = _end_window,
    alive: Callable[[int], bool] = pid_alive, stop: Callable[[], None] = _stop_started,
    end: Callable[[], None] = lambda: os._exit(0),
) -> None:
    """The window, then the server, its Neo4j and the search model: everything the app runs."""
    said = read_server_file(home, alive=lambda pid: True)
    if said is not None and said.window_pid and alive(said.window_pid):
        with contextlib.suppress(OSError):  # gone already
            kill(said.window_pid)
    stop()
    agent_file.remove_server_file(home)
    end()


def first_close(home: Path) -> bool:
    """Whether the window is closed for the first time -- said once, then remembered."""
    path = home / NOTICES
    try:
        notices = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notices = {}
    if not isinstance(notices, dict) or notices.get("background"):
        return False
    notices["background"] = True
    path.write_text(json.dumps(notices), encoding="utf-8")
    return True


def front(pid: int) -> bool:
    """The window of process `pid` brought to the front; False when it has none to bring."""
    try:
        if sys.platform == "darwin":
            import AppKit  # type: ignore[import-not-found,import-untyped,unused-ignore]

            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                return False
            # macOS 14 lets an app come forward only when the app in front yields to it: the
            # icon's menu, just clicked, is what is in front.
            me = AppKit.NSApplication.sharedApplication()
            if hasattr(me, "yieldActivationToApplication_"):
                me.yieldActivationToApplication_(app)
            return bool(app.activateWithOptions_(AppKit.NSApplicationActivateAllWindows))
        if sys.platform == "win32":  # pragma: no cover -- Windows only
            return _front_windows(pid)
    except Exception:  # noqa: BLE001 -- a new window, then
        log.exception("could not bring the window forward")
    return False


def _front_windows(pid: int) -> bool:  # pragma: no cover -- Windows only
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
    found: list[int] = []

    @ctypes.WINFUNCTYPE(  # type: ignore[attr-defined,untyped-decorator,unused-ignore]
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return False
    user32.ShowWindow(found[0], 9)  # SW_RESTORE
    return bool(user32.SetForegroundWindow(found[0]))


def _show(home: Path) -> Callable[[str], bool]:
    from graphme.launcher import show_window

    return lambda url: show_window(url, home / "webview")


def _tell(text: str) -> None:
    """`text` in a dialog of the system's own, in a process of its own, the window already gone.
    Asked of the closing window, the dialog waited on the thread closing it and the window
    froze; waited for here, the closed window stayed drawn until it was answered."""
    try:
        if sys.platform == "darwin":
            quoted = text.replace("\\", "\\\\").replace('"', '\\"')
            processes.popen(["osascript", "-e", f'display dialog "{quoted}" buttons {{"OK"}} '
                             'default button 1 with title "Graph-Me-Maybe" with icon note'],
                            start_new_session=True)
        elif sys.platform == "win32":  # pragma: no cover -- Windows only
            processes.popen([_python(), "-c", "import ctypes, sys; ctypes.windll.user32."
                             "MessageBoxW(None, sys.argv[1], 'Graph-Me-Maybe', 0x40)", text])
    except Exception:  # noqa: BLE001 -- told nothing; the app runs on all the same
        log.exception("could not say the app keeps running")


def open_window(
    home: Path, *, connect: Callable[[Path], httpx.Client] = agent_file.connect,
    show: Callable[[str], bool] | None = None, front: Callable[[int], bool] = front,
    tell: Callable[[str], None] = _tell,
) -> None:
    """The app's window on the agent -- started first when none runs -- until it is closed;
    one already open is brought forward instead. Closed the first time, it says the app keeps
    running behind its icon."""
    if sys.platform == "darwin" and show is None:
        # The window is the app: it checks in with macOS before the agent it starts does, or the
        # agent is taken for the app and opening the app again only wakes the agent.
        with contextlib.suppress(ImportError):
            import AppKit  # type: ignore[import-not-found,import-untyped,unused-ignore]

            AppKit.NSApplication.sharedApplication()
    connect(home).close()
    said = read_server_file(home)
    if said is None:
        raise SystemExit("Graph-Me-Maybe did not start")
    if said.window_pid and pid_alive(said.window_pid) and front(said.window_pid):
        return
    url = f"http://127.0.0.1:{said.port}"
    agent_file.set_window_pid(home, os.getpid())
    try:
        shown = (show or _show(home))(url)
        if not shown:
            processes.open_in_browser(url)  # no webview here (Linux): the browser, once
    finally:
        agent_file.set_window_pid(home, None)
    if shown and first_close(home):
        tell(BACKGROUND_NOTICE)


def _open_app(home: Path) -> None:
    """The window, from the icon: the installed app on macOS so the Dock shows its own icon."""
    if sys.platform == "darwin" and APP_BUNDLE.is_dir():
        processes.popen(["open", str(APP_BUNDLE)])
    else:
        processes.popen([_python(), "-m", "graphme.launcher", str(home)])


class _Icon:
    """The icon and its menu, its state read from the agent's own /v1."""

    def __init__(self, home: Path, client: httpx.Client) -> None:
        self.home, self.client = home, client
        self.state = self.read()
        self.icon: Any = None

    def read(self) -> AgentState:
        try:
            listed = self.client.get("/v1/workspaces", timeout=5.0).json()
        except (httpx.HTTPError, ValueError):
            listed = {}
        names = {i["id"]: i["name"] for i in listed.get("items", [])}
        return AgentState(names.get(listed.get("active")), listed.get("mcp_enabled", True),
                          login_item.enabled())

    def act(self, action: str) -> None:
        try:
            if action == "open":
                said = read_server_file(self.home)
                if not (said and said.window_pid and pid_alive(said.window_pid)
                        and front(said.window_pid)):
                    _open_app(self.home)
            elif action == "mcp":
                self.client.put("/v1/mcp", json={"enabled": not self.state.mcp_enabled})
            elif action == "login":
                if self.state.login:
                    login_item.disable()
                else:
                    login_item.enable(agent_command(self.home))
            elif action == "quit":
                self.icon.stop()
                quit_completely(self.home)
        except Exception:  # noqa: BLE001 -- the menu stays; the log says why
            log.exception("the menu's %s failed", action)
        self.refresh()

    def refresh(self) -> None:
        state = self.read()
        if state != self.state:
            self.state = state
            if self.icon is not None:
                self.icon.update_menu()

    def run(self) -> None:
        import pystray  # type: ignore[import-not-found,import-untyped,unused-ignore]
        from PIL import Image

        def item(entry: MenuEntry) -> Any:
            if entry.action is None:
                return pystray.MenuItem(entry.text, None, enabled=False)
            action = entry.action
            checked = (lambda _: entry.checked) if action in ("mcp", "login") else None
            return pystray.MenuItem(entry.text, lambda: self.act(action), checked=checked,
                                    default=action == "open")

        def items() -> Iterator[Any]:
            return (item(entry) for entry in agent_menu(self.state))

        picture = _menu_bar_mark() if sys.platform == "darwin" else Image.open(
            Path(__file__).parent / "icons" / "AppIcon.ico").convert("RGBA").resize((64, 64))
        self.icon = pystray.Icon("graph-me-maybe", picture, "Graph-Me-Maybe",
                                 menu=pystray.Menu(items))
        if sys.platform == "darwin":
            # The menu bar only: no Dock icon, no app menu -- the window has its own.
            import AppKit  # type: ignore[import-not-found,import-untyped,unused-ignore]

            AppKit.NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory

        def setup(icon: Any) -> None:
            icon.visible = True
            if sys.platform == "darwin":
                # A template image: macOS draws it in the menu bar's own colour, light or dark.
                with contextlib.suppress(AttributeError):
                    icon._icon_image.setTemplate_(True)
            while True:  # the workspace switched in the window, MCP switched off
                time.sleep(STATE_EVERY_SECONDS)
                self.refresh()

        self.icon.run(setup=setup)


def _menu_bar_mark() -> Any:
    """The app's mark -- three nodes and their links, as in logo.svg -- in black on nothing,
    for a menu bar that colours it itself."""
    from PIL import Image, ImageDraw

    scale = 44 / 26
    picture = Image.new("RGBA", (44, 44))
    draw = ImageDraw.Draw(picture)

    def at(x: float, y: float) -> tuple[float, float]:
        return x * scale, y * scale

    for end in ((17.5, 8), (16, 17.5)):
        draw.line([at(8.5, 9), at(*end)], fill="black", width=3)
    for x, y, r in ((8.5, 9, 3.2), (17.5, 8, 2.6), (16, 17.5, 3.2)):
        draw.ellipse([at(x - r, y - r), at(x + r, y + r)], fill="black")
    return picture


def run_agent(home: Path) -> None:
    """The agent: the server on a free port, `server.json` once it answers, then the icon on
    this (the main) thread until "Quit completely". One already running is left to run."""
    from graphme.launcher import free_port, prepare, web_directory
    from graphme.server.demo import run

    if (said := read_server_file(home)) is not None and agent_file.answers(said):
        return
    prepare(home)
    os.chdir(home)  # settings and data are read relative to the working directory
    port, token = free_port(), secrets.token_urlsafe(32)
    ready = threading.Event()

    def serving() -> None:
        try:
            run(port, web_directory(home), on_ready=ready.set, token=token)
        except Exception:  # noqa: BLE001 -- said in the log; the wait below gives up
            log.exception("the server stopped")

    server = threading.Thread(target=serving, daemon=True)
    server.start()
    while not ready.wait(0.2):
        if not server.is_alive():
            raise SystemExit(f"Graph-Me-Maybe did not start -- see {home / 'app.log'}")
    agent_file.write_server_file(home, port=port, token=token)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}",
                          headers={"Authorization": f"Bearer {token}"})
    try:
        _Icon(home, client).run()
    except Exception:  # noqa: BLE001 -- no icon here (no GUI session): the server alone
        log.exception("no icon; the app runs until ended")
        server.join()
    quit_completely(home)

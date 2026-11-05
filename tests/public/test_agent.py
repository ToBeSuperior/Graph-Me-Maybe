"""The agent behind its icon, and the window as a process of its own."""
from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Any

import httpx

from graphme import agent, agent_file, login_item


def test_the_menu_says_the_state_and_offers_each_switch() -> None:
    entries = agent.agent_menu(agent.AgentState(workspace="Invented main", mcp_enabled=True,
                                                login=False))

    assert entries[0] == agent.MenuEntry("Running · Invented main · MCP on", None)
    assert [(e.text, e.action, e.checked) for e in entries[1:]] == [
        ("Open the app", "open", False),
        ("MCP on", "mcp", True),
        ("Start at login", "login", False),
        ("Quit completely", "quit", False),
    ]
    off = agent.agent_menu(agent.AgentState(workspace=None, mcp_enabled=False, login=True))
    assert off[0].text == "Running · MCP off"
    assert ("MCP on", False) in [(e.text, e.checked) for e in off]
    assert ("Start at login", True) in [(e.text, e.checked) for e in off]


def test_quit_completely_ends_the_window_then_everything_the_agent_started(
    tmp_path: Path,
) -> None:
    agent_file.write_server_file(tmp_path, port=8765, token="t")
    agent_file.set_window_pid(tmp_path, 4321)
    ended: list[Any] = []

    agent.quit_completely(tmp_path, kill=lambda pid: ended.append(("window", pid)),
                          alive=lambda pid: True, stop=lambda: ended.append("stopped"),
                          end=lambda: ended.append("ended"))

    assert ended == [("window", 4321), "stopped", "ended"]
    assert not (tmp_path / "server.json").exists()


def test_the_window_opens_on_the_running_agent_and_starts_none(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8799, token="t")
    shown: list[str] = []

    def connect(home: Path) -> httpx.Client:
        said = agent_file.read_server_file(home)
        assert said is not None
        return httpx.Client(base_url=f"http://127.0.0.1:{said.port}")

    agent.open_window(tmp_path, connect=connect, show=lambda url: shown.append(url) or True,
                      front=lambda pid: False, tell=lambda text: None)

    assert shown == ["http://127.0.0.1:8799"]
    # Closed: the agent's file says no window again, and keeps the rest.
    said = agent_file.read_server_file(tmp_path)
    assert said is not None and said.window_pid is None and said.port == 8799


def test_a_window_already_open_is_brought_forward_not_opened_twice(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8799, token="t")
    agent_file.set_window_pid(tmp_path, os.getpid())
    fronted: list[int] = []

    agent.open_window(tmp_path, connect=lambda home: httpx.Client(),
                      show=lambda url: (_ for _ in ()).throw(AssertionError("shown")),
                      front=lambda pid: fronted.append(pid) or True)

    assert fronted == [os.getpid()]


def test_the_first_close_is_told_once(tmp_path: Path) -> None:
    assert agent.first_close(tmp_path) is True
    assert agent.first_close(tmp_path) is False


def test_start_at_login_on_macos_is_a_launch_agent_naming_the_agent(tmp_path: Path) -> None:
    command = ["/invented/python", "-m", "graphme.launcher", "--agent", "/invented/home"]

    login_item.enable(command, platform="darwin", folder=tmp_path)

    plist = plistlib.loads((tmp_path / f"{login_item.LABEL}.plist").read_bytes())
    assert plist["ProgramArguments"] == command and plist["RunAtLoad"] is True
    assert login_item.enabled(platform="darwin", folder=tmp_path)
    login_item.disable(platform="darwin", folder=tmp_path)
    assert not login_item.enabled(platform="darwin", folder=tmp_path)


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = KEY_READ = REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def OpenKey(self, root: str, path: str, reserved: int = 0, access: int = 0) -> Any:  # noqa: N802
        assert path == login_item.RUN_KEY
        return self

    def __enter__(self) -> FakeWinreg:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def SetValueEx(self, key: Any, name: str, reserved: int, kind: int, value: str) -> None:  # noqa: N802
        self.values[name] = value

    def QueryValueEx(self, key: Any, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def DeleteValue(self, key: Any, name: str) -> None:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_start_at_login_on_windows_is_a_run_value() -> None:
    registry = FakeWinreg()
    command = [r"C:\Invented\pythonw.exe", "-m", "graphme.launcher", "--agent"]

    login_item.enable(command, platform="win32", registry=registry)

    assert registry.values == {
        "Graph-Me-Maybe": r"C:\Invented\pythonw.exe -m graphme.launcher --agent"}
    assert login_item.enabled(platform="win32", registry=registry)
    login_item.disable(platform="win32", registry=registry)
    login_item.disable(platform="win32", registry=registry)  # already off: nothing to do
    assert not login_item.enabled(platform="win32", registry=registry)


def test_the_first_close_is_told_after_the_window_is_gone_and_once(tmp_path: Path) -> None:
    # Told from the window's own closing handler, the dialog waited on the thread that was
    # closing the window, and the window froze.
    agent_file.write_server_file(tmp_path, port=8799, token="t")
    told: list[str] = []
    events: list[str] = []

    def show(url: str) -> bool:
        events.append("shown")
        return True

    def tell(text: str) -> None:
        events.append("told")
        told.append(text)

    for _ in range(2):
        agent.open_window(tmp_path, connect=lambda home: httpx.Client(), show=show,
                          front=lambda pid: False, tell=tell)

    assert events == ["shown", "told", "shown"] and told == [agent.BACKGROUND_NOTICE]


def test_quit_completely_takes_the_icon_away_before_the_process_ends(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """The process ends at once (`os._exit`), so a stop only posted to the icon's loop never
    took it out of the notification area: Windows left it there until a pointer passed over."""
    said: list[Any] = []

    class Icon:
        def __setattr__(self, name: str, value: Any) -> None:
            said.append((name, value))

        def stop(self) -> None:
            said.append("stop")

    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": [], "mcp_enabled": True})))
    monkeypatch.setattr(agent.login_item, "enabled", lambda: False)
    monkeypatch.setattr(agent, "quit_completely", lambda home: said.append("ended"))
    icon = agent._Icon(tmp_path, client)
    icon.icon = Icon()

    icon.act("quit")

    assert said[:2] == [("visible", False), "stop"] and said.index("ended") > 1

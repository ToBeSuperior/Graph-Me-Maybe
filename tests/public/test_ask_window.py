"""`graphme ask-window`: the files picked with a right-click, asked about in a small window."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from graphme import agent, agent_file, ask_window


def agent_answering(scope: dict[str, Any], asked: list[tuple[str, Any]]) -> Any:
    """A `connect` reaching an agent that answers `scope` and begins conversation `t1`."""
    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        asked.append((f"{request.method} {request.url.path}", body))
        if request.url.path == "/v1/scope":
            return httpx.Response(200, json=scope)
        if request.url.path == "/v1/threads":
            return httpx.Response(200, json={"id": "t1", "workspace": "w", "created_at": 0,
                                             "updated_at": 0})
        return httpx.Response(404)

    return lambda home: httpx.Client(base_url="http://127.0.0.1:8799",
                                     transport=httpx.MockTransport(answer))


def test_the_files_are_scoped_then_a_conversation_begun_then_the_window_opened_on_it(
    tmp_path: Path,
) -> None:
    asked: list[tuple[str, Any]] = []
    shown: list[tuple[str, list[str]]] = []
    picked = tmp_path / "invented quote.pdf"
    picked.write_bytes(b"%PDF-invented")
    connect = agent_answering({"document_ids": ["d1"], "missing": [], "elsewhere": [],
                               "workspace": "w2", "build_into": "w2"}, asked)

    ask_window.main([str(picked)], tmp_path, connect=connect,
                    show=lambda url, home, paths: shown.append((url, paths)) or True,
                    browse=lambda url: None)

    assert asked == [("POST /v1/scope", {"paths": [str(picked.resolve())]}),
                     ("POST /v1/threads", {"scope": ["d1"], "workspace": "w2"})]
    # The window's address names the conversation, never a path; the paths go to the page apart.
    assert shown == [("http://127.0.0.1:8799/mini/t1", [str(picked.resolve())])]


def test_a_file_a_mail_workspace_holds_is_asked_there_and_nothing_is_built(
    tmp_path: Path,
) -> None:
    asked: list[tuple[str, Any]] = []
    connect = agent_answering({"document_ids": ["m1"], "missing": [], "elsewhere": [],
                               "workspace": "mail-1", "build_into": "main"}, asked)

    ask_window.main([str(tmp_path / "invented.eml")], tmp_path, connect=connect,
                    show=lambda url, home, paths: True, browse=lambda url: None)

    assert [a for a, _ in asked] == ["POST /v1/scope", "POST /v1/threads"]
    assert asked[1][1] == {"scope": ["m1"], "workspace": "mail-1"}


def test_files_no_graph_holds_begin_an_empty_conversation_where_they_may_be_built(
    tmp_path: Path,
) -> None:
    asked: list[tuple[str, Any]] = []
    connect = agent_answering({"document_ids": [], "missing": ["/invented/new.docx"],
                               "elsewhere": [], "workspace": None, "build_into": "main"}, asked)

    ask_window.main(["/invented/new.docx"], tmp_path, connect=connect,
                    show=lambda url, home, paths: True, browse=lambda url: None)

    # Asked about nothing until the page has built them -- never about every document.
    assert asked[1] == ("POST /v1/threads", {"scope": [], "workspace": "main"})
    assert not any("jobs" in a for a, _ in asked)  # the page builds, showing how far


def test_with_nothing_buildable_the_conversation_is_in_the_open_workspace(tmp_path: Path) -> None:
    asked: list[tuple[str, Any]] = []
    connect = agent_answering({"document_ids": [], "missing": ["/invented/new.docx"],
                               "elsewhere": [], "workspace": None, "build_into": None}, asked)

    ask_window.main(["/invented/new.docx"], tmp_path, connect=connect,
                    show=lambda url, home, paths: True, browse=lambda url: None)

    assert asked[1] == ("POST /v1/threads", {"scope": []})


def test_without_a_webview_the_browser_opens_the_same_address(tmp_path: Path) -> None:
    browsed: list[str] = []
    connect = agent_answering({"document_ids": ["d1"], "missing": [], "elsewhere": [],
                               "workspace": "w", "build_into": "w"}, [])

    ask_window.main(["/invented/a.pdf"], tmp_path, connect=connect,
                    show=lambda url, home, paths: False, browse=browsed.append)

    assert browsed == ["http://127.0.0.1:8799/mini/t1"]


def test_the_window_comes_back_where_it_was_left(tmp_path: Path) -> None:
    assert ask_window.place(tmp_path) == {"width": 420, "height": 560}

    class Window:
        x, y, width, height = 40, 60, 500, 640

    ask_window.keep_place(tmp_path, Window())
    assert ask_window.place(tmp_path) == {"x": 40, "y": 60, "width": 500, "height": 640}
    (tmp_path / ask_window.PLACE).write_text("{not json")
    assert ask_window.place(tmp_path) == {"width": 420, "height": 560}


def test_the_pin_keeps_the_window_on_top_or_lets_it_go(tmp_path: Path) -> None:
    class Window:
        on_top = True

    page = ask_window.MiniApi(tmp_path, ["/invented/a.pdf"])
    page._window = Window()

    page.pin(False)
    assert page._window.on_top is False
    page.pin(True)
    assert page._window.on_top is True
    assert page.paths() == ["/invented/a.pdf"]


def test_open_in_the_main_window_brings_an_open_one_to_the_conversation(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8799, token="t")
    agent_file.set_window_pid(tmp_path, os.getpid())
    fronted: list[int] = []
    started: list[Path] = []

    ask_window.open_main(tmp_path, "t1", front=lambda pid: fronted.append(pid) or True,
                         start=started.append)

    assert fronted == [os.getpid()] and started == []
    assert agent.take_goto(tmp_path) == "/c/t1"
    assert agent.take_goto(tmp_path) is None  # taken once


def test_open_in_the_main_window_starts_one_at_the_conversation(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8799, token="t")
    started: list[Path] = []

    ask_window.open_main(tmp_path, "t1", front=lambda pid: True, start=started.append)
    shown: list[str] = []
    agent.open_window(tmp_path, connect=lambda home: httpx.Client(),
                      show=lambda url: shown.append(url) or True,
                      front=lambda pid: False, tell=lambda text: None)

    assert started == [tmp_path]
    assert shown == ["http://127.0.0.1:8799/c/t1"]


def test_only_a_conversation_is_opened_in_the_main_window(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8799, token="t")
    ask_window.open_main(tmp_path, "../../manage/danger", front=lambda pid: True,
                         start=lambda home: None)

    assert agent.take_goto(tmp_path) is None


def test_the_windows_opacity_is_never_below_forty_percent_nor_above_whole() -> None:
    assert ask_window.clamped(0.1) == 0.4
    assert ask_window.clamped(1.5) == 1.0
    assert ask_window.clamped(0.65) == 0.65


class FakeNSWindow:
    """What pywebview's cocoa backend hands over as `window.native`."""

    def __init__(self) -> None:
        self.alpha: list[float] = []

    def setAlphaValue_(self, value: float) -> None:  # noqa: N802
        self.alpha.append(value)


class FakeForm:
    """What the WinForms backend hands over: the form itself."""

    Opacity = 1.0


def test_the_opacity_slider_sets_the_whole_native_window(tmp_path: Path) -> None:
    ran: list[str] = []

    def on_main(native: Any, work: Any) -> None:  # the window's own thread, as the backend asks
        ran.append(type(native).__name__)
        work()

    class Window:
        native: Any = FakeNSWindow()

    page = ask_window.MiniApi(tmp_path, [], on_main=on_main)
    page._window = Window()
    page.set_opacity(0.1)
    assert Window.native.alpha == [0.4] and ran == ["FakeNSWindow"]

    Window.native = FakeForm()
    page.set_opacity(0.75)
    assert Window.native.Opacity == 0.75

    Window.native = object()  # any other backend: nothing to set, nothing raised
    page.set_opacity(0.5)


def test_the_opacity_is_kept_with_the_place_and_restored(tmp_path: Path) -> None:
    class Window:
        x, y, width, height = 10, 20, 430, 570
        native: Any = FakeNSWindow()

    assert ask_window.kept_opacity(tmp_path) == 1.0
    page = ask_window.MiniApi(tmp_path, [], on_main=lambda native, work: work())
    page._window = Window()
    page.set_opacity(0.55)
    ask_window.keep_place(tmp_path, Window())  # closing keeps the place and the opacity both

    assert ask_window.kept_opacity(tmp_path) == 0.55
    assert ask_window.place(tmp_path) == {"x": 10, "y": 20, "width": 430, "height": 570}
    assert ask_window.MiniApi(tmp_path, []).opacity() == 0.55
    reopened = FakeNSWindow()
    ask_window.restore_opacity(tmp_path, reopened, on_main=lambda native, work: work())
    assert reopened.alpha == [0.55]


def test_a_place_on_no_screen_there_is_now_opens_where_the_system_puts_it(tmp_path: Path) -> None:
    class Screen:
        x, y, width, height = 0, 0, 1440, 900

    class Window:
        x, y, width, height = 3000, 100, 430, 570

    ask_window.keep_place(tmp_path, Window())
    assert ask_window.placed(tmp_path, [Screen()]) == {"width": 430, "height": 570}
    assert ask_window.placed(tmp_path, []) == {"width": 430, "height": 570}  # screens unknown
    Window.x = 200
    ask_window.keep_place(tmp_path, Window())
    assert ask_window.placed(tmp_path, [Screen()]) == {"x": 200, "y": 100, "width": 430,
                                                       "height": 570}


def test_ask_window_runs_on_the_home_the_settings_name(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    from typer.testing import CliRunner

    from graphme.cli import app

    dev = tmp_path / "invented-dev-home"
    (tmp_path / ".env").write_text(f"GRAPHME_HOME={dev}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GRAPHME_HOME", raising=False)
    homes: list[Path] = []
    monkeypatch.setattr(ask_window, "main", lambda paths, home: homes.append(home))

    result = CliRunner().invoke(app, ["ask-window", "/invented/a.pdf"])

    assert result.exit_code == 0, result.output
    assert homes == [dev]


def test_a_write_cut_short_leaves_the_kept_place_whole(tmp_path: Path,
                                                        monkeypatch: Any) -> None:
    import pytest

    ask_window._keep(tmp_path, opacity=0.8)

    import os

    def cut_short(*_: Any, **__: Any) -> None:
        raise OSError("no space left")  # the disk filled before the new place was put in
    monkeypatch.setattr(os, "replace", cut_short)
    with pytest.raises(OSError):
        ask_window._keep(tmp_path, opacity=0.5)
    monkeypatch.undo()

    assert ask_window.kept_opacity(tmp_path) == 0.8


def test_files_picked_at_once_by_separate_processes_open_one_window(tmp_path: Path) -> None:
    import threading

    results: dict[str, list[str] | None] = {}

    def one(name: str) -> None:
        results[name] = ask_window.collect(tmp_path, name, window=0.4)

    threads = [threading.Thread(target=one, args=(f"/files/{n}.pdf",)) for n in "abcd"]
    for t in threads:
        t.start()
        time.sleep(0.02)
    for t in threads:
        t.join()

    leaders = [v for v in results.values() if v is not None]
    assert len(leaders) == 1 and sorted(leaders[0]) == [f"/files/{n}.pdf" for n in "abcd"]
    assert list((tmp_path / ask_window.PENDING).iterdir()) == []
    assert not (tmp_path / ask_window.LOCK).exists()


def test_a_stale_collector_lock_does_not_swallow_the_next_pick(tmp_path: Path) -> None:
    lock = tmp_path / ask_window.LOCK
    lock.write_text("", encoding="utf-8")
    os.utime(lock, (1, 1))

    assert ask_window.collect(tmp_path, "/f/a.pdf", window=0) == ["/f/a.pdf"]

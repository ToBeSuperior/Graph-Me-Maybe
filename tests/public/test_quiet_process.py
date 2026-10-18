"""Every program the app starts runs without a window of its own. Nothing real is started."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from graphme import processes

SRC = Path(__file__).resolve().parents[2] / "src" / "graphme"


def test_on_windows_a_started_program_opens_no_console_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The window app has no console, so each `docker inspect` -- once per screen while Docker
    # was off -- flashed a black window of its own.
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: seen.append(kwargs))
    monkeypatch.setattr(processes.sys, "platform", "win32")
    processes.run(["docker", "inspect", "x"], capture_output=True)
    monkeypatch.setattr(processes.sys, "platform", "darwin")
    processes.run(["docker", "inspect", "x"], capture_output=True)
    assert seen[0]["creationflags"] == 0x08000000  # CREATE_NO_WINDOW
    assert "creationflags" not in seen[1]


def test_nothing_starts_a_program_but_through_it() -> None:
    direct = []
    for path in SRC.rglob("*.py"):
        if path == SRC / "processes.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "subprocess"
                    and node.attr in {"run", "Popen", "call", "check_call", "check_output"}):
                direct.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert direct == []


def test_text_from_a_program_is_read_as_utf8_whatever_the_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Korean Windows read docker's output as cp949 and a reader thread died on a UTF-8 byte.
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: seen.append(kwargs))
    processes.run(["docker", "start", "-a", "x"], capture_output=True, text=True)
    processes.run(["docker", "inspect", "x"], capture_output=True)
    assert seen[0]["encoding"] == "utf-8" and seen[0]["errors"] == "replace"
    assert "encoding" not in seen[1]  # bytes asked for stay bytes

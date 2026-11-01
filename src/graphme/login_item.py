"""Start at login: the agent started when the user logs in -- a LaunchAgent on macOS, a Run value
on Windows. Off until switched on from the icon's menu."""
from __future__ import annotations

import contextlib
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

LABEL = "io.github.tobesuperior.graph-me-maybe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Graph-Me-Maybe"


def _plist(folder: Path | None) -> Path:
    return (folder or Path.home() / "Library" / "LaunchAgents") / f"{LABEL}.plist"


def _winreg(registry: Any) -> Any:
    if registry is not None:
        return registry
    import winreg  # type: ignore[import-not-found,unused-ignore]  # Windows only

    return winreg


def enable(command: list[str], *, platform: str = sys.platform, folder: Path | None = None,
           registry: Any = None) -> None:
    """`command` run at each login."""
    if platform == "darwin":
        path = _plist(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(
            {"Label": LABEL, "ProgramArguments": command, "RunAtLoad": True}))
    elif platform == "win32":
        reg = _winreg(registry)
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, RUN_VALUE, 0, reg.REG_SZ, subprocess.list2cmdline(command))
    else:
        raise OSError("start at login is set on macOS and Windows only")


def disable(*, platform: str = sys.platform, folder: Path | None = None,
            registry: Any = None) -> None:
    if platform == "darwin":
        _plist(folder).unlink(missing_ok=True)
    elif platform == "win32":
        reg = _winreg(registry)
        with (reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key,
              contextlib.suppress(FileNotFoundError)):  # off already
            reg.DeleteValue(key, RUN_VALUE)


def enabled(*, platform: str = sys.platform, folder: Path | None = None,
            registry: Any = None) -> bool:
    if platform == "darwin":
        return _plist(folder).is_file()
    if platform == "win32":
        reg = _winreg(registry)
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
            try:
                reg.QueryValueEx(key, RUN_VALUE)
            except FileNotFoundError:
                return False
            return True
    return False

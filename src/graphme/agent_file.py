"""Where the running app is found: `server.json` in its home.

The agent -- the app running in the background, behind its menu bar or tray icon -- writes it when
it starts and removes it when it ends: its port, its pid, its version and a token every client
sends. `graphme mcp` reads it to reach the app; the window adds its own pid, so the installers and
"Quit completely" can end both. Only its user reads it: the token is what lets a client in.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import httpx

from graphme import __version__
from graphme.processes import popen

FILE = "server.json"
START_WAIT_SECONDS = 90.0


@dataclass(frozen=True)
class ServerFile:
    port: int
    pid: int
    version: str
    token: str
    window_pid: int | None = None


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid runs now."""
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover -- Windows only
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # someone else's: alive all the same
        return True
    except OverflowError:
        return False
    return True


def _write(home: Path, said: ServerFile) -> None:
    home.mkdir(parents=True, exist_ok=True)
    path = home / FILE
    fresh = path.with_name(f"{FILE}.{os.getpid()}")
    fresh.write_text(json.dumps(asdict(said)), encoding="utf-8")
    fresh.chmod(0o600)
    fresh.replace(path)


def write_server_file(home: Path, *, port: int, token: str) -> ServerFile:
    """This process as the agent answering on `port`."""
    said = ServerFile(port=port, pid=os.getpid(), version=__version__, token=token)
    _write(home, said)
    return said


def read_server_file(home: Path, *, alive: Callable[[int], bool] = pid_alive) -> ServerFile | None:
    """The agent running now, or None: no file, one that cannot be read, or one left by an
    agent that is gone."""
    try:
        raw = json.loads((home / FILE).read_text(encoding="utf-8"))
        said = ServerFile(port=int(raw["port"]), pid=int(raw["pid"]), version=str(raw["version"]),
                          token=str(raw["token"]),
                          window_pid=int(raw["window_pid"]) if raw.get("window_pid") else None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return said if alive(said.pid) else None


def set_window_pid(home: Path, pid: int | None) -> None:
    """The window open now (or none), beside the agent that serves it."""
    said = read_server_file(home)
    if said is not None:
        _write(home, replace(said, window_pid=pid))


def remove_server_file(home: Path) -> None:
    """The file gone with this agent -- never another agent's."""
    said = read_server_file(home, alive=lambda pid: True)
    if said is not None and said.pid == os.getpid():
        (home / FILE).unlink(missing_ok=True)


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """`path` locked against every other process (and thread) taking it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as held:
        if sys.platform == "win32":  # pragma: no cover -- Windows only
            import msvcrt

            held.seek(0)
            while True:
                try:
                    msvcrt.locking(held.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ten seconds: ask again
                    continue
            try:
                yield
            finally:
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(held, fcntl.LOCK_EX)
            yield  # let go when the file closes


def start_agent(home: Path) -> None:
    """The app started in the background (its icon appears), apart from this process."""
    # A session of its own: the client ending `graphme mcp` does not end the app.
    detached = {} if sys.platform == "win32" else {"start_new_session": True}
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "app.log", "ab") as log:  # what it says, where the window's goes
        # Not as the app: macOS gives the window the app's bundle id in __CFBundleIdentifier,
        # and the agent that inherited it was taken for the app -- opening the app again from
        # the Dock or the Applications folder only woke the agent, and no window came.
        env = {k: v for k, v in os.environ.items() if k != "__CFBundleIdentifier"}
        popen([sys.executable, "-m", "graphme.launcher", "--agent"],
              env={**env, "GRAPHME_HOME": str(home)},
              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **detached)


def answers(said: ServerFile) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{said.port}/health", timeout=2.0).is_success
    except httpx.HTTPError:
        return False


def connect(home: Path, *, spawn: Callable[[Path], None] = start_agent,
            answers: Callable[[ServerFile], bool] = answers,
            wait: float = START_WAIT_SECONDS) -> httpx.Client:
    """A client of the agent `home`'s `server.json` names, started first when none runs -- once,
    however many `graphme mcp` ask at the same time (`agent.lock`)."""
    said = read_server_file(home)
    if said is None or not answers(said):
        with _locked(home / "agent.lock"):
            if read_server_file(home) is None:
                spawn(home)
            until = time.monotonic() + wait
            while (said := read_server_file(home)) is None or not answers(said):
                if time.monotonic() > until:
                    raise RuntimeError(f"Graph-Me-Maybe did not start within {wait:.0f} s")
                time.sleep(0.2)
    return httpx.Client(base_url=f"http://127.0.0.1:{said.port}",
                        headers={"Authorization": f"Bearer {said.token}"}, timeout=600.0)

"""Every program this one starts. Started from the window app, which has no console, a
console program (neo4j, python) gets a console window of its own on Windows -- one flashing
up per call -- so it is started without one."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Mapping
from typing import Any

CREATE_NO_WINDOW = 0x08000000  # Windows' own value; `subprocess` names it only on Windows


def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        # Read as what the program writes, not the system's code page: Korean Windows read
        # a program's UTF-8 as cp949, and the reader thread died on the first byte it could not.
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(args, **kwargs)


def popen(args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    """A program left running (the app's Neo4j): no console window either."""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.Popen(args, **kwargs)


def open_in_browser(
    url: str, *, environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., object] = run,
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

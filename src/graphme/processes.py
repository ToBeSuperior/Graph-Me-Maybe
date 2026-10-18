"""Every program this one starts. Started from the window app, which has no console, a
console program (docker, python) gets a console window of its own on Windows -- one flashing
up per call -- so it is started without one."""
from __future__ import annotations

import subprocess
import sys
from typing import Any

CREATE_NO_WINDOW = 0x08000000  # Windows' own value; `subprocess` names it only on Windows


def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        # Read as what the program writes, not the system's code page: Korean Windows read
        # docker's UTF-8 as cp949, and the reader thread died on the first byte it could not.
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(args, **kwargs)

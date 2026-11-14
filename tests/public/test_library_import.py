"""`import graphme` is the library alone: what serves pages, draws a window or speaks MCP is
not imported by it, so those can move to extras without breaking an `import graphme`."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import graphme

APP_ONLY = ("fastapi", "uvicorn", "starlette", "multipart", "webview", "pystray", "mcp", "keyring")


def test_import_graphme_pulls_in_nothing_the_app_alone_needs() -> None:
    loaded = subprocess.run(
        [sys.executable, "-c", "import sys, graphme; print(' '.join(sys.modules))"],
        capture_output=True, text=True, check=True).stdout.split()

    assert not {name.split(".")[0] for name in loaded} & set(APP_ONLY)


def test_the_package_says_it_is_typed() -> None:
    assert (Path(graphme.__file__).parent / "py.typed").is_file()

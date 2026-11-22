"""`graphme doctor`: what the app, a build and an MCP client each need, found as the app itself
finds it, and one fix for each that is missing.

Read only: it starts, writes and fetches nothing, and prints no key or token.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from graphme import agent_file, mcp_install, model_endpoint
from graphme.adapters.local_models import LocalModels
from graphme.adapters.storage.local_neo4j import Neo4jHome
from graphme.config import Settings

KEYS_PAGE = "open the app, Manage › Keys and model"
LABELS = {"claude-code": "Claude Code", "claude-desktop": "Claude Desktop", "cursor": "Cursor"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    problem: str
    """What is wrong; empty when `ok`."""
    fix: str
    """One command or menu path; empty when `ok`."""
    note: str = ""
    """Something to know that is not a problem."""


def _check(name: str, problem: str, fix: str) -> Check:
    return Check(name, not problem, problem, "" if not problem else fix)


def _command_exists(command: str) -> bool:
    if os.path.isabs(command):
        return Path(command).is_file() and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def _playwright_chromium(environ: Mapping[str, str], user_home: Path, platform: str) -> bool:
    """Playwright's own Chromium, which draws pages when no Chrome is installed."""
    if named := environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        root = Path(named)
    elif platform == "darwin":
        root = user_home / "Library" / "Caches" / "ms-playwright"
    elif platform == "win32":
        local = environ.get("LOCALAPPDATA") or user_home / "AppData" / "Local"
        root = Path(local) / "ms-playwright"
    else:
        root = user_home / ".cache" / "ms-playwright"
    return bool(glob.glob(str(root / "chromium*")))


def _search_model(settings: Settings) -> str:
    """The problem `preflight` would name for the search model, or ''."""
    row = model_endpoint.EMBEDDERS.get(settings.embedding_provider)
    if row is None:
        return ""
    if row.model_file is not None:
        models = LocalModels(settings.local_models or settings.data_dir)
        return "" if models.installed(row.model_file) else "the search model is not installed"
    if not settings.embedding_key:
        return f"the search API key ({settings.embedding_provider}) is empty"
    return ""


def _client(client: str, user_home: Path, platform: str, environ: Mapping[str, str]) -> Check:
    path = mcp_install.config_path(client, user_home=user_home, platform=platform, environ=environ)
    name = f"MCP in {LABELS[client]}"
    fix = f"graphme mcp install --client {client}"
    present = path.exists() or (path.parent.exists() and client != "claude-code") or (
        client == "claude-code" and (user_home / ".claude").exists())
    if not present:
        return Check(name, True, "", "", f"not found here (looked in {path}); "
                     f"to register anyway: {fix}")
    try:
        entry = mcp_install.registered_entry(path)
    except mcp_install.ConfigError:
        return _check(name, f"{path} is not valid JSON; fix or remove it, then install", fix)
    if entry is None:
        return _check(name, f"graph-me-maybe is not in {path}", fix)
    if not entry.get("command"):
        return Check(name, True, "", "", "registered (not a command entry)")
    command = str(entry["command"])
    return _check(name, "" if _command_exists(command)
                  else f"registered command {command} does not exist", fix)


def checks(
    home: Path, settings: Settings, *, user_home: Path | None = None,
    platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
    answers: Callable[[agent_file.ServerFile], bool] = agent_file.answers,
) -> list[Check]:
    """Every check, in the order a setup goes: the app, what it needs, then each MCP client."""
    user_home = user_home or Path.home()
    said = agent_file.read_server_file(home)
    app = ""
    if said is not None and not answers(said):
        app = f"the app (pid {said.pid}) does not answer"
    local = settings.local_neo4j
    neo4j = "" if local is None or Neo4jHome(local).installed() else "no Neo4j in the app's home"
    chat = (model_endpoint.KEY_REQUIRED and settings.api_key is None
            and f"the {settings.provider} API key is empty")
    viewer = "" if (settings.viewer_directory / "dist").is_dir() else (
        f"no viewer bundle in {settings.viewer_directory}")
    chrome = settings.chrome_executable
    browser = "" if (chrome and Path(chrome).is_file()) or _playwright_chromium(
        environ, user_home, platform) else "no Chrome or Chromium to draw pages"
    return [
        _check("app", app, "quit the app from its menu bar or tray icon (Quit completely), "
               "then open it again") if said is not None else Check(
            "app", True, "", "", "not running; `graphme mcp` starts it when a client asks"),
        _check("graph database", neo4j, "graphme setup"),
        _check("search model", _search_model(settings), KEYS_PAGE + " › Search model"),
        _check("model key", chat or "", KEYS_PAGE),
        _check("viewer", viewer, "graphme setup"),
        _check("browser", browser, "graphme setup"),
        *(_client(c, user_home, platform, environ) for c in mcp_install.CLIENTS),
    ]

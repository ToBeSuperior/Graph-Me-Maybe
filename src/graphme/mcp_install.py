"""`graphme mcp install`: the `graphme mcp` entry added to an MCP client's own config file.

Only the `mcpServers` key is touched; every other key and entry stays as it was, the file is
copied to `<name>.bak` before it changes, and a file that is not a JSON object is refused
whole rather than rewritten.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from graphme.files import write_atomic

CLIENTS = ("claude-code", "claude-desktop", "cursor")
NAME = "graph-me-maybe"
_TEMPORARY = ("/archive-v", "/builds-v", "pipx/.cache")
"""Parts of a path that mark a throw-away environment (`uvx`, `pipx run`), gone later."""


class ConfigError(Exception):
    """A client's config file this module will not change, or an entry it cannot make."""


def _stable(path: Path) -> bool:
    return not any(mark in path.as_posix() for mark in _TEMPORARY)


def current_entry(*, which: Callable[[str], str | None] = shutil.which, argv0: str | None = None,
                  executable: str | None = None) -> dict[str, Any]:
    """The entry that starts `graphme mcp` as this install runs it, by absolute path -- a GUI
    client has no shell PATH. In order: the script running now, the `graphme` on the PATH, this
    Python with `-m graphme.cli mcp`; each only if it is stable (a throw-away `uvx` environment
    is gone later, and so is a Python inside it). A link is kept, not resolved: a Homebrew link
    outlives the upgrade of what it points at."""
    argv0 = sys.argv[0] if argv0 is None else argv0
    candidates = [argv0] if Path(argv0).stem == "graphme" and Path(argv0).is_file() else []
    if found := which("graphme"):
        candidates.append(found)
    for candidate in candidates:
        if _stable(script := Path(candidate).absolute()):
            return {"command": str(script), "args": ["mcp"]}
    python = Path(executable or sys.executable).absolute()
    if _stable(python):
        return {"command": str(python), "args": ["-m", "graphme.cli", "mcp"]}
    raise ConfigError("this graphme runs from a temporary environment that will be gone; install "
                      "it to stay (uv tool install graph-me-maybe, or the app installer), then "
                      "run this command again")


def config_path(client: str, *, user_home: Path | None = None, platform: str = sys.platform,
                environ: Mapping[str, str] = os.environ) -> Path:
    """Where `client` keeps its MCP servers on this system."""
    home = user_home or Path.home()
    if client == "claude-code":
        return home / ".claude.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "claude-desktop":
        name = "claude_desktop_config.json"
        if platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / name
        if platform == "win32":
            return Path(environ.get("APPDATA") or home / "AppData" / "Roaming") / "Claude" / name
        return home / ".config" / "Claude" / name
    raise ValueError(f"not a client: {client} (one of {', '.join(CLIENTS)})")


def _read(path: Path) -> dict[str, Any]:
    """The file's JSON object; {} when there is no file. Anything else is refused."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfigError(f"{path} cannot be read: {error}") from error
    try:
        said = json.loads(text)
    except ValueError as error:
        raise ConfigError(f"{path} is not valid JSON; left as it is") from error
    if not isinstance(said, dict) or not isinstance(said.get("mcpServers", {}), dict):
        raise ConfigError(f"{path} is not a JSON object with an mcpServers object; left as it is")
    return said


def registered_entry(path: Path) -> dict[str, Any] | None:
    """The file's Graph-Me-Maybe entry, or None. Raises `ConfigError` for a file it cannot read."""
    entry = _read(path).get("mcpServers", {}).get(NAME)
    return entry if isinstance(entry, dict) else None


def registered_scopes(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Claude Code's per-project (local scope) entries, `claude mcp add` writes there: each as
    ("project <path>", entry)."""
    projects = _read(path).get("projects")
    found: list[tuple[str, dict[str, Any]]] = []
    for where, project in (projects.items() if isinstance(projects, dict) else ()):
        servers = project.get("mcpServers") if isinstance(project, dict) else None
        entry = servers.get(NAME) if isinstance(servers, dict) else None
        if isinstance(entry, dict):
            found.append((f"project {where}", entry))
    return found


def installed(entry: Mapping[str, Any], wanted: Mapping[str, Any]) -> bool:
    """Whether `entry` is already `wanted`: the same command and arguments, no URL-style key
    left beside them, and that program still on disk."""
    command = entry.get("command")
    return (all(entry.get(k) == v for k, v in wanted.items())
            and not any(k in entry for k in ("type", "url", "headers"))
            and isinstance(command, str) and Path(command).is_file()
            and os.access(command, os.X_OK))


def registered(path: Path) -> bool:
    return registered_entry(path) is not None


def install(client: str, *, user_home: Path | None = None, platform: str = sys.platform,
            environ: Mapping[str, str] = os.environ,
            entry: dict[str, Any] | None = None) -> Path:
    """The entry written into `client`'s config -- once, however often this runs -- after the
    file is copied to `.bak` (the first copy is kept); the file's path. An entry already there
    is replaced by exactly the wanted shape (a stale URL-style entry's `type`/`url`/`headers`
    must not survive beside `command`); only its `env`, the user's own, is kept."""
    wanted = entry or current_entry()
    shown = config_path(client, user_home=user_home, platform=platform, environ=environ)
    path = shown.resolve()  # a symlinked file stays a link; its target is updated
    said = _read(path)
    servers = said.setdefault("mcpServers", {})
    old = servers.get(NAME)
    if isinstance(old, dict) and installed(old, wanted):
        return shown
    env = old.get("env") if isinstance(old, dict) else None
    servers[NAME] = {**wanted, **({"env": env} if isinstance(env, dict) else {})}
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    write_atomic(path, json.dumps(said, ensure_ascii=False, indent=2))
    return shown

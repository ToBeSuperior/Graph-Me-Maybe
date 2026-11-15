"""The app's workspaces: graphs kept apart in the home's one Neo4j, one open at a time.

Neo4j Community serves one user database, so every workspace's graph is in it under the
workspace's own labels and indexes (`WorkspaceScope`, `W_<id>`); a workspace's folder holds
only its crops and its mail. The first workspace is the home's own (`default`). What is
listed, and which is open, is `workspaces.json` in the home; which folders' graphs from before
were moved into the one Neo4j, `moved.json`. Keys, the model, the search model and the drives
connected are the machine's, shared by all.
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from graphme.server.jobs import Busy

FIRST = "default"
"""The home's own graph: the workspace every graph made before workspaces is."""
FIRST_NAME = "Default"
NAME_LIMIT = 60
OPEN_NOT_DELETED = "the workspace open now cannot be deleted -- open another first"


class Workspaces:
    """The workspaces kept in `home`, and which one is open."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._file = home / "workspaces.json"
        self._lock = threading.Lock()

    def listed(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    @property
    def active(self) -> str:
        return str(self.listed()["active"])

    def graph(self, workspace: str) -> Path | None:
        """`workspace`'s folder -- its crops, its mail, and a graph from before every workspace
        was in the one Neo4j (`neo4j-data/`); None for the home's own."""
        return None if workspace == FIRST else self.home / "workspaces" / workspace

    def item(self, workspace: str) -> dict[str, Any]:
        found = next((i for i in self.listed()["items"] if i["id"] == workspace), None)
        if found is None:
            raise LookupError(f"no such workspace: {workspace}")
        return dict(found)

    def create(self, name: str, *, kind: str = "documents",
               account: str | None = None) -> dict[str, Any]:
        """A workspace made. `kind` "mail" is one a mail `account` is read into, and nothing
        else is."""
        with self._lock:
            said = self._read()
            item = {"id": uuid.uuid4().hex[:12], "name": self._fresh(said, name), "mcp": False,
                    "kind": kind, "account": account}
            said["items"].append(item)
            self._write(said)
            return item

    def rename(self, workspace: str, name: str) -> dict[str, Any]:
        with self._lock:
            said = self._read()
            item = self._find(said, workspace)
            item["name"] = self._fresh(said, name, workspace)
            self._write(said)
            return dict(item)

    def release(self, workspace: str) -> dict[str, Any]:
        """A mail workspace whose account was disconnected, kept: an ordinary one from now on."""
        with self._lock:
            said = self._read()
            item = self._find(said, workspace)
            item["kind"], item["account"] = "documents", None
            self._write(said)
            return dict(item)

    def set_mcp(self, workspace: str, on: bool) -> dict[str, Any]:
        """Whether an MCP client may read `workspace`: off until switched on."""
        with self._lock:
            said = self._read()
            item = self._find(said, workspace)
            item["mcp"] = on
            self._write(said)
            return dict(item)

    @property
    def mcp_enabled(self) -> bool:
        """The one switch for all: off, no MCP client reads anything, whatever each allows."""
        return bool(self.listed()["mcp_enabled"])

    def set_mcp_enabled(self, on: bool) -> None:
        with self._lock:
            said = self._read()
            said["mcp_enabled"] = on
            self._write(said)

    def set_mcp_changes(self, on: bool) -> None:
        """Whether an MCP client may change things -- delete, reset, settings -- as well as read:
        off until switched on."""
        with self._lock:
            said = self._read()
            said["mcp_changes"] = on
            self._write(said)

    def activate(self, workspace: str) -> None:
        with self._lock:
            said = self._read()
            self._find(said, workspace)
            said["active"] = workspace
            self._write(said)

    def delete(self, workspace: str) -> None:
        """`workspace` unlisted and its folder deleted -- its graph is taken out of the one
        Neo4j first (`Demo.delete_workspace`). The one open cannot be. The home's own has no
        folder: its crops are swept as unused."""
        with self._lock:
            said = self._read()
            self._find(said, workspace)
            if workspace == said["active"]:
                raise Busy(OPEN_NOT_DELETED)
            said["items"] = [i for i in said["items"] if i["id"] != workspace]
            self._write(said)  # listed no more before its files go: never one half deleted
        graph = self.graph(workspace)
        if graph is not None:
            shutil.rmtree(graph, ignore_errors=True)

    def moved(self) -> set[str]:
        """The workspaces whose graph in their own folder was copied into the one Neo4j."""
        with self._lock:
            try:
                return {str(i) for i in json.loads(
                    (self.home / "moved.json").read_text(encoding="utf-8"))}
            except (OSError, ValueError, TypeError):
                return set()

    def mark_moved(self, workspace: str) -> None:
        said = sorted(self.moved() | {workspace})
        with self._lock:
            kept = self.home / "moved.tmp"
            kept.write_text(json.dumps(said), encoding="utf-8")
            kept.replace(self.home / "moved.json")

    def refuse_open(self, workspace: str) -> None:
        """Busy for the workspace open now; LookupError for one not listed."""
        self.item(workspace)
        if workspace == self.active:
            raise Busy(OPEN_NOT_DELETED)

    def _read(self) -> dict[str, Any]:
        try:
            said = json.loads(self._file.read_text(encoding="utf-8"))
            if said.get("items"):
                return {"active": str(said.get("active") or said["items"][0]["id"]),
                        "mcp_enabled": bool(said.get("mcp_enabled", True)),
                        "mcp_changes": bool(said.get("mcp_changes", False)),
                        "items": [{"id": str(i["id"]), "name": str(i["name"]),
                                   "mcp": bool(i.get("mcp", False)),
                                   # Written before mail was a source: documents.
                                   "kind": str(i.get("kind") or "documents"),
                                   "account": i.get("account")}
                                  for i in said["items"]]}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return {"active": FIRST, "mcp_enabled": True, "mcp_changes": False,
                "items": [{"id": FIRST, "name": FIRST_NAME, "mcp": False,
                           "kind": "documents", "account": None}]}

    def _write(self, said: dict[str, Any]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        kept = self._file.with_suffix(".tmp")
        kept.write_text(json.dumps(said, ensure_ascii=False, indent=1), encoding="utf-8")
        kept.replace(self._file)

    @staticmethod
    def _find(said: dict[str, Any], workspace: str) -> dict[str, Any]:
        found: dict[str, Any] | None = next(
            (i for i in said["items"] if i["id"] == workspace), None)
        if found is None:
            raise LookupError(f"no such workspace: {workspace}")
        return found

    @staticmethod
    def _fresh(said: dict[str, Any], name: str, renaming: str | None = None) -> str:
        """`name` as kept: trimmed, not empty, not too long, and no other workspace's."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("a workspace needs a name")
        if len(name) > NAME_LIMIT:
            raise ValueError(f"a workspace's name is at most {NAME_LIMIT} characters")
        if any(i["name"].casefold() == name.casefold() and i["id"] != renaming
               for i in said["items"]):
            raise ValueError(f"a workspace is called {name} already")
        return name

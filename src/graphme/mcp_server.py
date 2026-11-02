"""`graphme mcp`: an MCP server on stdio over the running app's /v1.

A client (Claude Code, Claude Desktop, Cursor) starts it as a child; each tool is a call on the
agent's API, so the client sees the graph, the workspaces and the builds the app shows, and no
second Neo4j or search model is started. The agent is found by `server.json` (`agent_file.py`) and
started when none runs. What a tool returns goes to the client's model: a workspace is read only
once switched on for MCP in the app, and nothing at all while MCP is off from the icon's menu.
Every answer is held under a size -- passages cut, an outline cut at a depth and a count, lists
paged -- so a client takes it whole.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

from graphme.agent_file import connect

PASSAGE_CHARS = 1200
NODE_CHARS = 4000
LABEL_CHARS = 80
OUTLINE_NODES = 400
LIST_LIMIT = 50
OFF = ("MCP is turned off in Graph-Me-Maybe: switch it on from its icon's menu, or in "
       "Manage > Keys and model")
FINISHED = ("idle", "done", "cancelled", "error")

INSTRUCTIONS = """Graph-Me-Maybe keeps documents (PDF, Word, PowerPoint, Excel) as graphs: each
document's outline as the page reads, the passages it is cut into, and the documents it cites.
Start with `search`; read a document's outline (`get_outline`) and a part of it (`read_node`)
by the ids the answers give. Workspaces are graphs kept apart; each is read only once switched
on for MCP in the app."""


def _cut(text: str, at: int) -> str:
    return text if len(text) <= at else f"{text[:at]} (+{len(text) - at} characters)"


def _said(response: httpx.Response) -> Any:
    """The answer's JSON, or the app's refusal as the tool's error."""
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ToolError(f"Graph-Me-Maybe answered {response.status_code}: {detail}")
    return response.json() if response.headers.get("content-type", "").startswith(
        "application/json") else response.content


def _kind(node: dict[str, Any]) -> str:
    labels = node["labels"]
    return str(labels[1] if len(labels) > 1 else labels[0])


def _label(node: dict[str, Any]) -> str:
    p = node["properties"]
    said = p.get("text") if _kind(node) == "TextChunk" else None
    said = said or p.get("label") or p.get("cell_range") or ""
    return _cut(" ".join(str(said).split()), LABEL_CHARS)


def _page(node: dict[str, Any]) -> int | None:
    page = node["properties"].get("meta_page")
    return int(page) + 1 if page is not None else None


class _Outline:
    """A document's graph read as its outline: each node's one parent."""

    def __init__(self, graph: dict[str, Any]) -> None:
        self.nodes = {node["id"]: node for node in graph["nodes"]}
        self.items: dict[str, list[str]] = {}
        held: set[str] = set()
        for r in graph["relationships"]:
            # A caption has no place in the outline; it hangs off what it describes.
            parent, child = ((str(r["start"]), str(r["end"])) if r["type"] == "PARENT_OF"
                             else (str(r["end"]), str(r["start"])) if r["type"] == "CAPTION_OF"
                             else ("", ""))
            if parent in self.nodes and child in self.nodes and child not in held:
                self.items.setdefault(parent, []).append(child)
                held.add(child)
        self.roots = [n for n in self.nodes if n not in held]

    def line(self, node_id: str, depth: int) -> str:
        node = self.nodes[node_id]
        page = f" p.{_page(node)}" if _page(node) is not None else ""
        return f"{'  ' * depth}[{node_id}] {_kind(node)}{page}: {_label(node)}"

    def text(self, max_depth: int) -> str:
        lines: list[str] = []
        stack = [(root, 0) for root in reversed(self.roots)]
        while stack and len(lines) < OUTLINE_NODES:
            node_id, depth = stack.pop()
            items = self.items.get(node_id, [])
            line = self.line(node_id, depth)
            if items and depth >= max_depth:
                line += f" (+{len(items)} items)"
            else:
                stack += [(item, depth + 1) for item in reversed(items)]
            lines.append(line)
        if stack:
            left = sum(1 + self._under(node_id) for node_id, _ in stack)
            lines.append(f"+{left} more nodes: read_node a part, or a smaller max_depth")
        return "\n".join(lines)

    def _under(self, node_id: str) -> int:
        return sum(1 + self._under(item) for item in self.items.get(node_id, []))


def build_server(client: httpx.Client) -> FastMCP:
    """The tools, each a call on the agent's /v1 through `client`."""
    server = FastMCP("graph-me-maybe", instructions=INSTRUCTIONS)

    def get(path: str, **params: Any) -> Any:
        return _said(client.get(path, params={k: v for k, v in params.items() if v is not None}))

    def post(path: str, body: Any = None) -> Any:
        return _said(client.post(path, json=body))

    def workspaces() -> dict[str, Any]:
        listed: dict[str, Any] = get("/v1/workspaces")
        if not listed.get("mcp_enabled", True):
            raise ToolError(OFF)
        return listed

    def allowed(workspace: str | None) -> tuple[str | None, bool]:
        """The workspace's id -- by id or name, the open one when not given -- and whether it
        is the open one; refused when MCP may not read it."""
        listed = workspaces()
        if not listed.get("available"):  # a server on another graph DB: its one graph
            if workspace is not None:
                raise ToolError("this Graph-Me-Maybe keeps no workspaces")
            return None, True
        items = listed["items"]
        if workspace is None:
            found = [i for i in items if i["id"] == listed["active"]]
        else:
            found = [i for i in items if i["id"] == workspace] \
                or [i for i in items if i["name"] == workspace]
        if not found:
            names = ", ".join(repr(i["name"]) for i in items)
            raise ToolError(f"no workspace {workspace!r}; there are {names}")
        if len(found) > 1:
            ids = ", ".join(i["id"] for i in found)
            raise ToolError(
                f"{len(found)} workspaces are named {workspace!r}: give one's id ({ids})")
        chosen = found[0]
        if not chosen.get("mcp"):
            raise ToolError(f"MCP may not read the workspace {chosen['name']!r}: switch it on "
                            "in Graph-Me-Maybe, Manage > Keys and model")
        return chosen["id"], chosen["id"] == listed["active"]

    def graph(document_id: str, workspace: str | None) -> _Outline:
        wid, _ = allowed(workspace)
        return _Outline(get(f"/v1/documents/{document_id}/graph", workspace=wid))

    @server.tool()
    def list_workspaces() -> dict[str, Any]:
        """Every workspace (a graph kept apart), whether MCP may read it, and the one open in
        the app -- what any tool's `workspace` is when not given."""
        listed = workspaces()
        return {"mcp_enabled": True, "workspaces": [
            {"id": i["id"], "name": i["name"], "mcp": bool(i.get("mcp")),
             "open": i["id"] == listed.get("active")} for i in listed.get("items", [])]}

    @server.tool()
    def search(question: str, top_k: int = 5, workspace: str | None = None) -> dict[str, Any]:
        """The passages that answer a question, best first, top_k documents' worth, each with
        the documents linked to its own (cites, cited_by, similar). No model writes an answer:
        read the passages, then get_outline / read_node for more."""
        wid, _ = allowed(workspace)
        found = post("/v1/search", {"question": question, "documents": top_k, "workspace": wid})
        return {"text_only": found.get("text_only", False), "sources": [
            {"document_id": s["document_id"], "file": s["file"], "score": round(s["score"], 3),
             "passage_id": s["id"], "text": _cut(s["context"], PASSAGE_CHARS),
             "linked": [{"document_id": d["document_id"], "file": d["file"], "hop": d["hop"],
                         "direction": d["direction"], "via": d["via"]}
                        for d in s.get("linked", [])]}
            for s in found["sources"]]}

    @server.tool()
    def ask(question: str, top_k: int = 5, workspace: str | None = None) -> dict[str, Any]:
        """An answer written by the model set in Graph-Me-Maybe (it costs that model's calls),
        with the passages it was written from."""
        wid, _ = allowed(workspace)
        answer = post("/v1/ask", {"question": question, "documents": top_k, "workspace": wid})
        return {"answer": answer["answer"], "text_only": answer.get("text_only", False),
                "sources": [{"document_id": s["document_id"], "file": s["file"],
                             "passage_id": s["id"]} for s in answer["sources"]]}

    @server.tool()
    def list_documents(filter: str | None = None, limit: int = LIST_LIMIT,  # noqa: A002
                       cursor: str | None = None, workspace: str | None = None) -> dict[str, Any]:
        """The documents in a workspace's graph, a page at a time (`next_cursor`); `filter`
        keeps those whose file name holds it."""
        wid, _ = allowed(workspace)
        items = [i for i in get("/v1/documents", workspace=wid)["items"] if i["in_space"]]
        if filter:
            items = [i for i in items if filter.casefold() in i["name"].casefold()]
        start, limit = int(cursor or 0), max(1, min(limit, LIST_LIMIT))
        page = items[start:start + limit]
        return {"total": len(items),
                "next_cursor": str(start + limit) if start + limit < len(items) else None,
                "documents": [{"document_id": i["document_id"], "name": i["name"],
                               "format": i["format"]} for i in page]}

    @server.tool()
    def get_outline(document_id: str, max_depth: int = 3,
                    workspace: str | None = None) -> dict[str, Any]:
        """A document's outline as the page reads, indented, each line `[node id] kind p.page:
        text`; what lies deeper than max_depth is counted (+N)."""
        outline = graph(document_id, workspace)
        if not outline.nodes:
            raise ToolError(f"no document {document_id!r} in the graph")
        return {"outline": outline.text(max_depth), "nodes_total": len(outline.nodes)}

    @server.tool()
    def read_node(document_id: str, node_id: str,
                  workspace: str | None = None) -> dict[str, Any]:
        """One part of a document: its whole text and what it holds."""
        outline = graph(document_id, workspace)
        node = outline.nodes.get(node_id)
        if node is None:
            raise ToolError(f"no node {node_id!r} in the document {document_id!r}")
        p = node["properties"]
        items = outline.items.get(node_id, [])
        return {"id": node_id, "kind": _kind(node), "page": _page(node),
                "text": _cut(str(p.get("text") or p.get("label") or ""), NODE_CHARS),
                "holds": [{"id": i, "kind": _kind(outline.nodes[i]),
                           "label": _label(outline.nodes[i])} for i in items[:LIST_LIMIT]],
                "holds_more": max(0, len(items) - LIST_LIMIT)}

    @server.tool()
    def linked_documents(document_id: str, workspace: str | None = None) -> dict[str, Any]:
        """The documents this one cites, those citing it, and those most like it."""
        wid, _ = allowed(workspace)
        mapped = get("/v1/graph/map", workspace=wid)
        files = {d["id"]: d["label"] for d in mapped["documents"]}
        linked = [{"document_id": other, "file": files.get(other, ""), "direction": way}
                  for link in mapped["links"]
                  for other, way in ((link["to"], "cites"), (link["from"], "cited_by"))
                  if document_id in (link["from"], link["to"]) and other != document_id]
        linked += [{"document_id": other, "file": files.get(other, ""), "direction": "similar",
                    "score": round(link["score"], 3)}
                   for link in mapped["similar"]
                   for other in (link["from"], link["to"])
                   if document_id in (link["from"], link["to"]) and other != document_id]
        return {"linked": linked}

    @server.tool()
    def get_page_image(document_id: str, page: int, workspace: str | None = None) -> Image:
        """A page of a document as drawn, page 1 first (the p.N get_outline shows). Only from
        the workspace open in the app: open_workspace first for another."""
        _, open_one = allowed(workspace)
        if not open_one:
            raise ToolError("pages are drawn only for the workspace open in the app: "
                            "open_workspace first")
        file_id = post(f"/v1/documents/{document_id}/file")["file_id"]
        return Image(data=_said(client.get(f"/v1/files/{file_id}/pages/{page - 1}/image")),
                     format="png")

    @server.tool()
    def add_folder(path: str) -> dict[str, Any]:
        """A folder or file on this machine read into the open workspace's graph, as the app's
        build does (it costs the model's calls); build_status says how far it is."""
        allowed(None)
        if not Path(path).expanduser().exists():
            raise ToolError(f"nothing at {path} on this machine")
        built: dict[str, Any] = post("/v1/jobs/build", {"paths": [str(Path(path).expanduser())]})
        return built

    def builds() -> list[dict[str, Any]]:
        return [job for job in get("/v1/jobs") if job.get("kind") in ("build", "embed")]

    @server.tool()
    def build_status() -> dict[str, Any]:
        """The open workspace's latest build: its state and how many documents are read."""
        allowed(None)
        jobs = builds()
        if not jobs:
            return {"state": "idle"}
        return jobs[-1]

    @server.tool()
    def cancel_build() -> dict[str, Any]:
        """The running build stopped: documents not yet read are passed over, what is in the
        graph stays."""
        allowed(None)
        running = [job for job in builds() if job.get("state") not in FINISHED]
        if not running:
            raise ToolError("no build is running")
        cancelled: dict[str, Any] = post(f"/v1/jobs/{running[-1]['id']}/cancel")
        return cancelled

    @server.tool()
    def open_workspace(workspace: str) -> dict[str, Any]:
        """Another workspace opened in the app (a few seconds), so pages and writing go to it."""
        wid, _ = allowed(workspace)
        post(f"/v1/workspaces/{wid}/open")
        return {"open": wid}

    return server


def run(home: Path) -> None:
    """The MCP server on stdio, over the agent of `home`."""
    build_server(connect(home)).run("stdio")

"""`graphme mcp`: each tool a call on the agent's /v1 (a fake one here), held to a size, and
refused where MCP may not read."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from graphme import agent_file, mcp_server

PNG = b"\x89PNG\r\n\x1a\ninvented"


class FakeV1:
    """The agent's /v1 with invented workspaces and documents; what each call sent is kept."""

    def __init__(self) -> None:
        self.mcp_enabled = True
        self.mcp_changes = False
        self.sent: list[tuple[str, str, Any]] = []
        self.nodes = 3

    def workspaces(self) -> dict[str, Any]:
        return {"available": True, "active": "main", "mcp_enabled": self.mcp_enabled,
                "mcp_changes": self.mcp_changes, "items": [
            {"id": "main", "name": "Invented main", "mcp": True},
            {"id": "w-closed", "name": "Closed notes", "mcp": False},
            {"id": "w-side", "name": "Side notes", "mcp": True},
            {"id": "w-a", "name": "Twin", "mcp": True},
            {"id": "w-b", "name": "Twin", "mcp": True},
        ]}

    def graph(self) -> dict[str, Any]:
        nodes = [{"id": "doc-1", "labels": ["DocNode", "Document"],
                  "properties": {"label": "Invented memo.docx"}}]
        nodes += [{"id": f"n{i}", "labels": ["DocNode", "TextChunk"],
                   "properties": {"text": f"Invented line {i}", "meta_page": 1}}
                  for i in range(self.nodes)]
        # n0 under the document, every other node under n0
        rels = [{"id": "r0", "type": "PARENT_OF", "start": "doc-1", "end": "n0", "properties": {}}]
        rels += [{"id": f"r{i}", "type": "PARENT_OF", "start": "n0", "end": f"n{i}",
                  "properties": {}} for i in range(1, self.nodes)]
        return {"nodes": nodes, "relationships": rels, "chunks": []}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        self.sent.append((method, str(request.url.copy_with(scheme="http")), body))
        if path == "/v1/workspaces":
            return httpx.Response(200, json=self.workspaces())
        if path == "/v1/search":
            return httpx.Response(200, json={"text_only": False, "sources": [{
                "id": "doc-1:0", "score": 0.9, "document_id": "doc-1", "file": "Invented memo.docx",
                "members": ["n0"], "context": "x" * 5000, "linked": [{
                    "document_id": "doc-2", "file": "Invented plan.pdf", "hop": 2, "via": "doc-1",
                    "direction": "cites", "passages": []}]}]})
        if path == "/v1/ask":
            return httpx.Response(200, json={"answer": "Invented answer.", "text_only": False,
                                             "sources": []})
        if path == "/v1/documents":
            return httpx.Response(200, json={"space_ok": True, "items": [
                {"name": f"Invented {i}.pdf", "format": "pdf", "in_space": True,
                 "document_id": f"doc-{i}", "file_id": None, "state": "idle"} for i in range(60)
            ] + [{"name": "Held only.pdf", "format": "pdf", "in_space": False, "file_id": "f",
                  "state": "idle"}]})
        if path == "/v1/documents/doc-1/graph":
            return httpx.Response(200, json=self.graph())
        if path == "/v1/graph/map":
            return httpx.Response(200, json={"total": 3, "documents": [
                {"id": "doc-1", "label": "Invented memo.docx"},
                {"id": "doc-2", "label": "Invented plan.pdf"},
                {"id": "doc-3", "label": "Invented note.xlsx"}],
                "links": [{"from": "doc-1", "to": "doc-2", "count": 2, "matched_by": ["name"]},
                          {"from": "doc-3", "to": "doc-1", "count": 1, "matched_by": ["name"]}],
                "similar": [{"from": "doc-2", "to": "doc-3", "score": 0.8}]})
        if path == "/v1/documents/doc-1/file":
            return httpx.Response(200, json={"file_id": "file-1"})
        if path == "/v1/files/file-1/pages/1/image":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if path == "/v1/documents/doc-1" and method == "DELETE":
            return httpx.Response(200, json={"deleted": "doc-1"})
        if path == "/v1/workspaces/w-side" and method == "DELETE":
            return httpx.Response(200, json=self.workspaces())
        if path == "/v1/graph/reset":
            return httpx.Response(200, json={"deleted": 19})
        if path == "/v1/settings" and method == "GET":
            return httpx.Response(200, json={"endpoint": "anthropic", "problems": [], "fields": [
                {"env": "GRAPHME_MODEL", "secret": False, "set": True, "value": "invented-model"},
                {"env": "ANTHROPIC_API_KEY", "secret": True, "set": True, "value": None}]})
        if path == "/v1/settings" and method == "PUT":
            return httpx.Response(200, json={"endpoint": "anthropic", "problems": [], "fields": []})
        if path == "/v1/documents/doc-1/open":
            return httpx.Response(200, json={"opened": "Invented memo.docx"})
        if path == "/v1/jobs/build":
            return httpx.Response(202, json={"id": "job-1", "kind": "build", "state": "reading"})
        if path == "/v1/jobs":
            return httpx.Response(200, json=[
                {"id": "job-0", "kind": "build", "state": "done", "done": 1, "total": 1},
                {"id": "job-1", "kind": "build", "state": "reading", "done": 2, "total": 5}])
        if path == "/v1/jobs/job-1/cancel":
            return httpx.Response(200, json={"id": "job-1", "kind": "build", "state": "cancelled"})
        if path == "/v1/workspaces/w-side/open":
            return httpx.Response(200, json=self.workspaces())
        return httpx.Response(404, json={"detail": f"no route {path}"})


@pytest.fixture
def v1() -> FakeV1:
    return FakeV1()


def call(v1: FakeV1, tool: str, **args: Any) -> tuple[bool, str, Any]:
    """The tool called through an MCP client session: whether it was refused, its text, and
    its first content."""
    server = mcp_server.build_server(
        httpx.Client(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(v1)))

    async def run() -> Any:
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(tool, args)

    result = anyio.run(run)
    first = result.content[0]
    return result.isError, getattr(first, "text", ""), first


def said(text: str) -> Any:
    return json.loads(text)


def test_every_workspace_is_listed_with_whether_mcp_may_read_it(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "list_workspaces")

    listed = said(text)
    assert not refused and listed["mcp_enabled"] is True
    assert {"id": "main", "name": "Invented main", "mcp": True, "open": True} \
        in listed["workspaces"]
    assert {"id": "w-closed", "name": "Closed notes", "mcp": False, "open": False} \
        in listed["workspaces"]


def test_a_search_is_the_app_s_search_its_passages_cut(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "search", question="invented", top_k=3, workspace="Side notes")

    found = said(text)
    assert not refused
    assert ("POST", "http://127.0.0.1:1/v1/search",
            {"question": "invented", "documents": 3, "workspace": "w-side"}) in v1.sent
    source = found["sources"][0]
    assert source["document_id"] == "doc-1" and source["passage_id"] == "doc-1:0"
    assert len(source["text"]) < 1300 and source["text"].endswith("(+3800 characters)")
    assert source["linked"] == [{"document_id": "doc-2", "file": "Invented plan.pdf", "hop": 2,
                                 "direction": "cites", "via": "doc-1"}]


def test_with_mcp_off_every_tool_is_refused(v1: FakeV1) -> None:
    v1.mcp_enabled = False

    for tool, args in [("list_workspaces", {}), ("search", {"question": "invented"})]:
        refused, text, _ = call(v1, tool, **args)
        assert refused and "MCP is turned off in Graph-Me-Maybe" in text
    assert not any("/v1/search" in url for _, url, _ in v1.sent)


def test_a_workspace_not_switched_on_is_refused_by_name(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "list_documents", workspace="Closed notes")

    assert refused and "Closed notes" in text
    assert not any("/v1/documents" in url for _, url, _ in v1.sent)


def test_a_name_two_workspaces_share_asks_for_the_id(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "search", question="invented", workspace="Twin")

    assert refused and "w-a" in text and "w-b" in text


def test_a_name_no_workspace_has_lists_the_names(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "search", question="invented", workspace="Nowhere")

    assert refused and "Invented main" in text and "Side notes" in text


def test_documents_are_listed_a_page_at_a_time(v1: FakeV1) -> None:
    _, text, _ = call(v1, "list_documents", limit=50)
    first = said(text)
    _, text, _ = call(v1, "list_documents", limit=50, cursor=first["next_cursor"])
    second = said(text)
    _, text, _ = call(v1, "list_documents", filter="invented 1")

    assert first["total"] == 60 and len(first["documents"]) == 50
    assert len(second["documents"]) == 10 and second["next_cursor"] is None
    assert {"document_id": "doc-1", "name": "Invented 1.pdf", "format": "pdf"} \
        in first["documents"]
    assert [d["name"] for d in said(text)["documents"]][:2] == ["Invented 1.pdf", "Invented 10.pdf"]


def test_an_outline_is_indented_text_with_ids_and_a_large_one_is_cut(v1: FakeV1) -> None:
    _, text, _ = call(v1, "get_outline", document_id="doc-1")
    small = said(text)["outline"]
    v1.nodes = 1000
    _, text, _ = call(v1, "get_outline", document_id="doc-1")
    large = said(text)

    assert small.splitlines()[:3] == ["[doc-1] Document: Invented memo.docx",
                                      "  [n0] TextChunk p.1: Invented line 0",
                                      "    [n1] TextChunk p.1: Invented line 1"]
    assert len(large["outline"].splitlines()) <= 402 and "+" in large["outline"].splitlines()[-1]
    assert large["nodes_total"] == 1001


def test_a_node_is_read_with_what_it_holds(v1: FakeV1) -> None:
    _, text, _ = call(v1, "read_node", document_id="doc-1", node_id="n0")

    node = said(text)
    assert node["text"] == "Invented line 0" and node["kind"] == "TextChunk" and node["page"] == 1
    assert {"id": "n1", "kind": "TextChunk", "label": "Invented line 1"} in node["holds"]


def test_linked_documents_say_which_way(v1: FakeV1) -> None:
    _, text, _ = call(v1, "linked_documents", document_id="doc-1")

    assert said(text)["linked"] == [
        {"document_id": "doc-2", "file": "Invented plan.pdf", "direction": "cites"},
        {"document_id": "doc-3", "file": "Invented note.xlsx", "direction": "cited_by"}]


def test_a_page_comes_as_an_image_and_only_from_the_open_workspace(v1: FakeV1) -> None:
    refused, _, image = call(v1, "get_page_image", document_id="doc-1", page=1)
    side, text, _ = call(v1, "get_page_image", document_id="doc-1", page=1, workspace="w-side")

    assert not refused and image.type == "image" and image.mimeType == "image/png"
    assert side and "open_workspace" in text


def test_writing_goes_to_the_open_workspace(v1: FakeV1, tmp_path: Path) -> None:
    _, built, _ = call(v1, "add_folder", path=str(tmp_path))
    missing, nowhere, _ = call(v1, "add_folder", path=str(tmp_path / "missing"))
    _, status, _ = call(v1, "build_status")
    _, cancelled, _ = call(v1, "cancel_build")
    _, opened, _ = call(v1, "open_workspace", workspace="Side notes")
    shut, closed, _ = call(v1, "open_workspace", workspace="Closed notes")

    assert ("POST", "http://127.0.0.1:1/v1/jobs/build",
            {"paths": [str(tmp_path)]}) in v1.sent and said(built)["id"] == "job-1"
    assert missing and "missing" in nowhere
    assert said(status)["id"] == "job-1" and said(cancelled)["state"] == "cancelled"
    assert said(opened)["open"] == "w-side"
    assert shut and "Closed notes" in closed


def test_connect_starts_the_agent_once_when_two_ask_at_once(tmp_path: Path) -> None:
    spawned: list[Path] = []

    def spawn(home: Path) -> None:
        spawned.append(home)

        def start() -> None:
            time.sleep(0.3)
            agent_file.write_server_file(home, port=8765, token="invented-token")

        threading.Thread(target=start).start()

    clients: list[httpx.Client] = []
    connect: Callable[[], None] = lambda: clients.append(  # noqa: E731
        agent_file.connect(tmp_path, spawn=spawn, answers=lambda said: True))
    threads = [threading.Thread(target=connect) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert spawned == [tmp_path] and len(clients) == 2
    assert clients[0].headers["authorization"] == "Bearer invented-token"
    assert str(clients[0].base_url) == "http://127.0.0.1:8765"


def test_connect_reaches_a_running_agent_without_starting_one(tmp_path: Path) -> None:
    agent_file.write_server_file(tmp_path, port=8766, token="t")

    client = agent_file.connect(tmp_path, spawn=lambda home: pytest.fail("started"),
                                answers=lambda said: True)

    assert str(client.base_url) == "http://127.0.0.1:8766"


def test_a_document_is_opened_in_its_own_app_on_this_machine(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "open_document", document_id="doc-1", workspace="Side notes")

    assert not refused and said(text) == {"opened": "Invented memo.docx"}
    assert ("POST", "http://127.0.0.1:1/v1/documents/doc-1/open?workspace=w-side", None) in v1.sent


def test_changes_are_refused_until_switched_on(v1: FakeV1) -> None:
    refused, text, _ = call(v1, "delete_document", document_id="doc-1")

    assert refused and "Allow changes" in text
    assert not any(method == "DELETE" for method, _, _ in v1.sent)


def test_a_deletion_waits_for_the_user_s_yes_and_its_code(v1: FakeV1) -> None:
    v1.mcp_changes = True
    server = mcp_server.build_server(
        httpx.Client(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(v1)))

    async def run() -> list[Any]:
        async with create_connected_server_and_client_session(server) as session:
            first = await session.call_tool("delete_document", {"document_id": "doc-1"})
            asked = json.loads(first.content[0].text)
            wrong = await session.call_tool("delete_document",
                                            {"document_id": "doc-1", "confirm": "nope"})
            done = await session.call_tool("delete_document",
                                           {"document_id": "doc-1", "confirm": asked["confirm"]})
            return [asked, json.loads(wrong.content[0].text), json.loads(done.content[0].text)]

    asked, wrong, done = anyio.run(run)

    assert "Invented memo.docx" in asked["would"] and asked["confirm"]
    assert wrong.get("confirm") and wrong["confirm"] != asked["confirm"]
    assert done == {"deleted": "doc-1"}
    assert [url for method, url, _ in v1.sent if method == "DELETE"] == [
        "http://127.0.0.1:1/v1/documents/doc-1"]


def test_settings_are_read_and_changed_but_never_a_key(v1: FakeV1) -> None:
    v1.mcp_changes = True
    _, shown, _ = call(v1, "get_settings")
    refused, text, _ = call(v1, "change_settings", values={"ANTHROPIC_API_KEY": "sk-invented"})
    _, changed, _ = call(v1, "change_settings", values={"GRAPHME_MODEL": "invented-other"})

    assert said(shown)["settings"] == {"GRAPHME_MODEL": "invented-model"}
    assert refused and "ANTHROPIC_API_KEY" in text and "in the app" in text
    assert ("PUT", "http://127.0.0.1:1/v1/settings",
            {"values": {"GRAPHME_MODEL": "invented-other"}}) in v1.sent
    assert not any(b and "sk-invented" in json.dumps(b) for _, _, b in v1.sent)

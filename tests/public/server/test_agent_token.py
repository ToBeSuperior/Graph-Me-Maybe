"""`graphme mcp` reaches the app with the token the agent wrote in `server.json`."""
from __future__ import annotations

from fastapi.testclient import TestClient

from graphme.server import create_app
from graphme.server.state import Demo


def test_the_agent_s_token_lets_a_client_in_beside_the_server_s_key(demo: Demo) -> None:
    demo.settings = demo.settings.model_copy(update={"server_key": "invented-server-key"})
    demo.agent_token = "invented-agent-token"
    client = TestClient(create_app(demo), base_url="http://127.0.0.1:8765")

    agent = {"Authorization": "Bearer invented-agent-token"}
    wrong = {"Authorization": "Bearer invented-but-wrong"}
    assert client.get("/v1/settings", headers=agent).status_code == 200
    assert client.get("/v1/settings", headers=wrong).status_code == 401


def test_with_no_server_key_this_machine_is_let_in_as_before(demo: Demo) -> None:
    demo.agent_token = "invented-agent-token"
    client = TestClient(create_app(demo), base_url="http://127.0.0.1:8765")

    assert client.get("/v1/settings").status_code == 200
    assert client.get("/v1/settings", headers={"Origin": "https://example.com"}).status_code == 403

"""`graphme serve`: what it opens to, and the contract it prints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from graphme import cli
from graphme.server import settings as server_settings


@pytest.fixture
def ran(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Where uvicorn would have served, without serving: started in `tmp_path`, no key."""
    import uvicorn

    started: list[dict[str, Any]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GRAPHME_API_KEY", raising=False)
    monkeypatch.setattr(server_settings, "_models", lambda url, headers: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **where: started.append(where))
    return started


def test_the_contract_is_printed_as_json() -> None:
    said = CliRunner().invoke(cli.app, ["serve", "--openapi"])

    assert said.exit_code == 0, said.output
    paths = json.loads(said.output)["paths"]
    assert "/health" in paths and "/v1/jobs/build" in paths


def test_without_a_key_it_opens_on_this_machine_only(ran: list[dict[str, Any]]) -> None:
    refused = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0"])

    assert refused.exit_code != 0 and "GRAPHME_API_KEY" in refused.output
    assert ran == []
    assert CliRunner().invoke(cli.app, ["serve"]).exit_code == 0
    assert ran == [{"host": "127.0.0.1", "port": 8765, "log_level": "info"}]


def test_with_a_key_it_opens_where_it_is_told(
    ran: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPHME_API_KEY", "invented-server-key")

    said = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert said.exit_code == 0, said.output
    assert ran == [{"host": "0.0.0.0", "port": 9000, "log_level": "info"}]

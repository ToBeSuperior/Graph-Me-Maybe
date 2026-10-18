"""Whose Neo4j answers on this machine's port. Docker is a fake; nothing is started."""
from __future__ import annotations

import json
import subprocess

import pytest

from graphme.adapters.storage import local_neo4j

URI = "bolt://127.0.0.1:7687"


def _docker(inspect: subprocess.CompletedProcess[str]):
    calls: list[list[str]] = []

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return inspect if args[1] == "inspect" else subprocess.CompletedProcess(args, 0, "", "")
    return run, calls


def test_with_docker_stopped_an_answering_neo4j_is_not_taken_for_ours() -> None:
    # Windows with Docker Desktop off reached WSL's Neo4j through localhost forwarding, and
    # the two apps shared one graph whose file paths only one of them can open.
    run, calls = _docker(subprocess.CompletedProcess(
        [], 1, "", "error during connect: open //./pipe/dockerDesktopLinuxEngine"))
    with pytest.raises(RuntimeError, match="Docker"):
        local_neo4j.ensure_local_neo4j(URI, "neo4j:5", ("1g", "1g"), run=run,
                                       answers=lambda host, port: True)
    assert [c[1] for c in calls] == ["inspect"]


def test_ours_stopped_while_another_answers_on_its_port_is_said() -> None:
    found = [{"Config": {"Env": ["NEO4J_AUTH=none"]}, "State": {"Running": False},
              "HostConfig": {"PortBindings": {"7687/tcp": [{"HostPort": "7687"}]}}}]
    run, _ = _docker(subprocess.CompletedProcess([], 0, json.dumps(found), ""))
    with pytest.raises(RuntimeError, match="7687"):
        local_neo4j.ensure_local_neo4j(URI, "neo4j:5", ("1g", "1g"), run=run,
                                       answers=lambda host, port: True)

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


@pytest.fixture(autouse=True)
def _fresh_docker_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_neo4j, "_off", local_neo4j.DockerOff())


_DOWN = subprocess.CompletedProcess(
    [], 1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")


def _ask(run, *, at: float, start_docker=None) -> Exception:
    with pytest.raises(RuntimeError) as said:
        local_neo4j.ensure_local_neo4j(URI, "neo4j:5", ("1g", "1g"), run=run,
                                       answers=lambda host, port: False,
                                       start_docker=start_docker, clock=lambda: at)
    return said.value


def test_docker_off_is_asked_once_for_a_burst_of_requests() -> None:
    # A screen change sends several requests; each ran `docker` again, about twice apiece.
    run, calls = _docker(_DOWN)
    first, second = _ask(run, at=0), _ask(run, at=1)
    assert len(calls) == 1 and str(first) == str(second)
    _ask(run, at=10)
    assert len(calls) == 2


def test_docker_desktop_is_started_once_and_the_wait_is_said() -> None:
    run, _ = _docker(_DOWN)
    started: list[int] = []

    def start() -> bool:
        started.append(1)
        return True

    assert isinstance(_ask(run, at=0, start_docker=start), local_neo4j.DockerStarting)
    assert isinstance(_ask(run, at=30, start_docker=start), local_neo4j.DockerStarting)
    late = _ask(run, at=local_neo4j.DOCKER_START_SECONDS + 1, start_docker=start)
    assert not isinstance(late, local_neo4j.DockerStarting) and "약관" in str(late)
    assert started == [1]

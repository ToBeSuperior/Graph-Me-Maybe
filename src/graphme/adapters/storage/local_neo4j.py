"""The graph space's own Neo4j on this machine, started when nothing answers at its address.

Used locally, the graph space needs no account: when the configured address is this
machine's and nothing listens there, a Neo4j container is started -- or the one started
before is started again, its data kept in a named volume -- with authentication off and its
one port, Bolt, open to this machine only, so nothing but this machine can reach it. A Neo4j
someone runs themselves, with a password, is left alone: it answers, so nothing is started.
Only an upload to Aura asks for an instance, a user and a password.
"""
from __future__ import annotations

import json
import logging
import math
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from graphme import processes
from graphme.config import LOOPBACK

_log = logging.getLogger(__name__)

CONTAINER = "graphme-neo4j"
VOLUME = "graphme-neo4j-data"
LOGS = "graphme-neo4j-logs"
WAIT_SECONDS = 120
_starting = threading.Lock()
DOCKER_START_SECONDS = 180  # Docker Desktop's own start, a minute or two
OFF_SECONDS = 5


class DockerStarting(RuntimeError):
    """Docker Desktop was started for the graph and is not up yet: ask again shortly."""


class DockerOff:
    """A stopped Docker as last seen, kept a few seconds -- a screen's requests come together,
    and each ran `docker` again -- and when Docker Desktop was started, which is done once."""

    def __init__(self) -> None:
        self.seen = -math.inf
        self.error: RuntimeError | None = None
        self.started: float | None = None

    def recent(self, now: float) -> RuntimeError | None:
        return self.error if now - self.seen < OFF_SECONDS else None

    def said(self, now: float, start_docker: Callable[[], bool] | None) -> RuntimeError:
        if self.started is None and start_docker is not None and start_docker():
            self.started = now
        error: RuntimeError
        if self.started is not None and now - self.started < DOCKER_START_SECONDS:
            error = DockerStarting(
                "Docker를 켜는 중입니다 -- 준비되면 저절로 이어집니다(보통 1~2분).")
        elif self.started is not None:
            error = RuntimeError(
                "Docker Desktop을 켰지만 준비되지 않았습니다. Docker Desktop 창에 약관 동의나 "
                "로그인 같은 확인이 떠 있는지 보세요.")
        else:
            error = RuntimeError(
                "Docker가 실행 중이 아닙니다. Docker Desktop(또는 colima)을 켠 뒤 다시 "
                "실행하세요. 직접 띄운 Neo4j라면 GRAPHME_NEO4J_PASSWORD를 설정하세요.")
        self.seen, self.error = now, error
        return error

    def up(self) -> None:
        self.error, self.started = None, None


_off = DockerOff()


def start_docker_desktop() -> bool:
    """Docker Desktop started, without waiting for it. Linux's Docker Engine is a service the
    app does not start."""
    try:
        if sys.platform == "win32":
            try:
                done = processes.run(["docker", "desktop", "start", "--detach"],
                                     capture_output=True, text=True, timeout=30)
                if done.returncode == 0:
                    return True
            except subprocess.TimeoutExpired:
                return True
            programs = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            desktop = programs / "Docker" / "Docker" / "Docker Desktop.exe"
            if desktop.is_file():
                os.startfile(desktop)  # a window program: no console either
                return True
            return False
        if sys.platform == "darwin":
            return processes.run(["open", "-a", "Docker"], capture_output=True).returncode == 0
    except OSError:
        return False
    return False


# Bolt's opening: the magic number, then four versions offered (5.x, then 4.4). Docker's own
# port forward takes a connection before Neo4j is up, so a port that only accepts is not one
# that answers: Neo4j picks a version back, four bytes.
_HANDSHAKE = bytes.fromhex("6060b017" "00000805" "00000404" "00000000" "00000000")


def _answers(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1) as connection:
            connection.sendall(_HANDSHAKE)
            return len(connection.recv(4)) == 4
    except OSError:
        return False


def ensure_local_neo4j(
    uri: str, image: str, memory: tuple[str, str], *,
    run: Callable[..., subprocess.CompletedProcess[str]] = processes.run,
    answers: Callable[[str, int], bool] = _answers,
    sleep: Callable[[float], None] = time.sleep,
    start_docker: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Whether a Neo4j had to be started for `uri`. Nothing is started for an address that
    is not this machine's, or where something already answers. `memory` is its heap and page
    cache; the one started before with other memory is made again, its data kept."""
    parts = urlsplit(uri)
    host, port = parts.hostname or "", parts.port or 7687
    if host not in LOOPBACK:
        return False
    # Two first uses at once -- the page asks for the list and the map together -- start one.
    with _starting:
        if (seen := _off.recent(clock())) is not None:
            raise seen
        up = answers(host, port)
        heap, pagecache = memory
        wanted = [f"NEO4J_server_memory_heap_max__size={heap}",
                  f"NEO4J_server_memory_pagecache_size={pagecache}",
                  # One made by hand with a password (an earlier README said how) is made again
                  # without, on the same data.
                  "NEO4J_AUTH=none"]
        try:
            known = run(["docker", "inspect", CONTAINER], capture_output=True, text=True)
        except FileNotFoundError as error:
            if up:
                return False  # a Neo4j run without Docker: someone else's to configure
            raise RuntimeError(
                "로컬 그래프 DB(Neo4j)를 띄우려면 Docker가 필요합니다. Docker를 설치·실행하거나 "
                "GRAPHME_NEO4J_URI로 직접 띄운 Neo4j를 가리키세요."
            ) from error
        if known.returncode != 0 and _daemon_down(known.stderr):
            # What answers cannot be told for ours -- WSL's, reached from Windows by its
            # localhost forwarding, looks the same -- and nothing can be started.
            raise _off.said(clock(), start_docker)
        _off.up()
        # Memory and authentication are fixed when a container is made: ours, made otherwise, is
        # made again on the same volume -- even while it is what answers. When something else
        # answers, ours is not running and is left as it is.
        env, running, published = (
            _inspected(known.stdout) if known.returncode == 0 else (set(), False, None))
        # Ours, published on another port, belongs to whatever uses that port: never moved here.
        if published is not None and published != port:
            if up:
                return False
            raise RuntimeError(
                f"Docker의 {CONTAINER} 컨테이너는 {published}번 포트에 묶여 있어 "
                f"{port}번에 띄울 수 없습니다. GRAPHME_NEO4J_URI를 {published}번 포트로 "
                "두거나, 다른 Docker(예: colima 프로필)를 쓰세요."
            )
        if up and known.returncode == 0 and not running:
            # Ours is stopped, so what answers on its port is another install's (an app under
            # WSL on the same PC) or someone's own Neo4j: never used as this app's graph.
            raise RuntimeError(
                f"{port}번 포트에서 이 앱의 것이 아닌 Neo4j가 답합니다 (같은 PC의 WSL이나 다른 "
                "설치의 앱일 수 있습니다). 그쪽을 끄거나 GRAPHME_NEO4J_URI를 다른 포트로 "
                "두세요.")
        stale = known.returncode == 0 and not set(wanted) <= env
        if up and not (stale and running):
            return False
        if stale:
            run(["docker", "rm", "-f", CONTAINER], capture_output=True, text=True)
            known = subprocess.CompletedProcess(known.args, 1, "", "")
        if known.returncode == 0:
            _log.warning("로컬 Neo4j(%s)를 다시 켜는 중입니다", CONTAINER)
            done = run(["docker", "start", CONTAINER], capture_output=True, text=True)
        else:
            _log.warning("로컬 Neo4j를 처음 띄우는 중입니다 -- "
                         "이미지를 받느라 1~2분 걸릴 수 있습니다")
            done = run([
                "docker", "run", "-d", "--name", CONTAINER,
                "-p", f"127.0.0.1:{port}:7687",
                "-v", f"{VOLUME}:/data",
                # Named, or each container made again leaves an anonymous one behind.
                "-v", f"{LOGS}:/logs",
                "-e", "NEO4J_dbms_usage__report_enabled=false",
                *(arg for setting in wanted for arg in ("-e", setting)),
                image,
            ], capture_output=True, text=True)
        if done.returncode != 0:
            said = (done.stderr or done.stdout).strip()[-400:]
            if known.returncode != 0:
                # A run that failed half way leaves its container; the next try starts afresh.
                run(["docker", "rm", "-f", CONTAINER], capture_output=True, text=True)
            raise RuntimeError(f"로컬 Neo4j를 띄우지 못했습니다: {said}")
        for _ in range(WAIT_SECONDS):
            if answers(host, port):
                _log.warning("로컬 Neo4j 준비됨 (%s)", uri)
                return True
            sleep(1)
        raise RuntimeError(
            f"로컬 Neo4j가 {WAIT_SECONDS}초 안에 응답하지 않았습니다. 컨테이너 상태는 "
            f"`docker logs {CONTAINER}`로 봅니다. 컨테이너가 떠 있는데도 이 컴퓨터의 "
            f"{port}번 포트로 답이 없으면 Docker의 포트 전달 문제입니다 -- colima라면 "
            "`colima stop && colima start --port-forwarder grpc` 후 다시 실행하세요."
        )


def _daemon_down(said: str) -> bool:
    """`docker` is there but its engine is not: Linux and macOS say "daemon", Windows
    "error during connect"."""
    said = said.lower()
    return "daemon" in said or "error during connect" in said or "docker api" in said


def own_port(
    run: Callable[..., subprocess.CompletedProcess[str]] = processes.run,
    free: Callable[[int], bool] | None = None,
) -> int:
    """Where ours is when this machine's usual port is someone else's Neo4j: the port it is
    published on, or for a new one the first free port after 7687."""
    try:
        known = run(["docker", "inspect", CONTAINER], capture_output=True, text=True)
    except FileNotFoundError:
        known = subprocess.CompletedProcess([], 1, "", "")
    if known.returncode == 0 and (published := _inspected(known.stdout)[2]) is not None:
        return published
    return next(port for port in range(7688, 7788) if (free or _free)(port))


def _free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _inspected(inspected: str) -> tuple[set[str], bool, int | None]:
    """A container's environment, whether it is running, and the port Bolt is published on."""
    try:
        found = json.loads(inspected)[0]
        bound = (found.get("HostConfig", {}).get("PortBindings") or {}).get("7687/tcp") or []
        published = int(bound[0]["HostPort"]) if bound else None
        return set(found["Config"]["Env"]), bool(found.get("State", {}).get("Running")), published
    except (ValueError, LookupError, TypeError, AttributeError):
        return set(), False, None

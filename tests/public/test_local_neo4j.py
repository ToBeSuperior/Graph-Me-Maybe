"""The app's own Neo4j, without Docker: fetched and checked, started on loopback with no
password, known by its data folder, stopped with its server. Nothing is downloaded or started:
the archives are invented and every process is a fake."""
from __future__ import annotations

import io
import json
import subprocess
import tarfile
import zipfile
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from graphme.adapters.storage import local_neo4j
from graphme.adapters.storage.local_neo4j import NEO4J_VERSION, Neo4jHome


def _tar(top: str, files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _zip(top: str, files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in files.items():
            archive.writestr(f"{top}/{name}", data)
    return out.getvalue()


class _Publishers:
    """Neo4j's and Adoptium's servers, as invented archives and their checksums."""

    def __init__(self, platform: str, *, bad: str | None = None) -> None:
        pack = _zip if platform == "win32" else _tar
        self.neo4j = pack(f"neo4j-community-{NEO4J_VERSION}", {"bin/neo4j": b"#!/bin/sh"})
        self.jre = pack("jdk-21-jre", {"bin/java": b"java"})
        self.bad = bad
        self.fetched: list[str] = []

    def fetch(self, url: str, target: Path) -> None:
        self.fetched.append(url)
        target.write_bytes(self.neo4j if "neo4j" in url else self.jre)

    def read(self, url: str) -> bytes:
        if url.endswith(".sha256"):
            digest = sha256(b"other" if self.bad == "neo4j" else self.neo4j).hexdigest()
            return f"{digest}  neo4j.tar.gz\n".encode()
        digest = sha256(b"other" if self.bad == "jre" else self.jre).hexdigest()
        return json.dumps([{"binary": {"package": {
            "name": "jre.zip" if "windows" in url else "jre.tar.gz",
            "link": "https://example.invalid/jre", "checksum": digest}}}]).encode()


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_neo4j_and_java_are_fetched_checked_and_unpacked_into_the_home(
    tmp_path: Path, platform: str
) -> None:
    sources = _Publishers(platform)
    home = local_neo4j.install(tmp_path, platform=platform, machine="x86_64",
                               fetch=sources.fetch, read=sources.read, say=lambda _: None)
    assert (home.neo4j / "bin" / "neo4j").read_bytes() == b"#!/bin/sh"
    assert (home.jre / "bin" / "java").is_file() and home.installed()
    assert any(f"-{'windows.zip' if platform == 'win32' else 'unix.tar.gz'}" in u
               for u in sources.fetched)
    local_neo4j.install(tmp_path, fetch=sources.fetch, read=sources.read, say=lambda _: None)
    assert len(sources.fetched) == 2  # there already: nothing fetched again


@pytest.mark.parametrize("bad", ["neo4j", "jre"])
def test_an_archive_that_does_not_match_its_checksum_installs_nothing(
    tmp_path: Path, bad: str
) -> None:
    sources = _Publishers("linux", bad=bad)
    with pytest.raises(RuntimeError, match="checksum"):
        local_neo4j.install(tmp_path, platform="linux", machine="x86_64",
                            fetch=sources.fetch, read=sources.read, say=lambda _: None)
    assert not Neo4jHome(tmp_path).installed() and not (tmp_path / "neo4j").exists()


def test_the_graph_is_kept_when_neo4j_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "neo4j-data" / "db").mkdir(parents=True)
    (tmp_path / "neo4j-data" / "db" / "graph").write_text("kept")
    sources = _Publishers("linux")
    local_neo4j.install(tmp_path, platform="linux", machine="x86_64",
                        fetch=sources.fetch, read=sources.read, say=lambda _: None)
    assert (tmp_path / "neo4j-data" / "db" / "graph").read_text() == "kept"


def test_it_runs_on_loopback_with_no_password_and_nothing_else_open() -> None:
    said = local_neo4j.conf(Path(r"C:\Users\a\AppData\Local\Graph-Me-Maybe\neo4j-data"), 7688,
                            ("1g", "512m"))
    lines = dict(line.split("=", 1) for line in said.splitlines())
    assert lines["server.bolt.listen_address"] == "127.0.0.1:7688"
    assert lines["dbms.security.auth_enabled"] == "false"
    assert lines["server.http.enabled"] == "false" and lines["server.https.enabled"] == "false"
    assert lines["dbms.usage_report.enabled"] == "false"
    # Neo4j reads a backslash as an escape: Windows' folders are written with slashes.
    assert lines["server.directories.data"] == (
        "C:/Users/a/AppData/Local/Graph-Me-Maybe/neo4j-data/db")
    assert (lines["server.memory.heap.max_size"], lines["server.memory.pagecache.size"]) == (
        "1g", "512m")


class _Child:
    pid = 4242

    def __init__(self, ends: bool = False) -> None:
        self.ends = ends
        self.waited = False

    def poll(self) -> int | None:
        return 1 if self.ends else None

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_it_starts_as_a_child_with_no_window_and_its_own_settings(
    tmp_path: Path, platform: str
) -> None:
    started: list[tuple[list[str], dict[str, Any]]] = []

    def popen(args: list[str], **options: Any) -> _Child:
        started.append((args, options))
        return _Child()

    home = Neo4jHome(tmp_path)
    local_neo4j.start(home, home.data, 7688, ("1g", "512m"), popen=popen,  # type: ignore[arg-type]
                      platform=platform)
    (args, options), = started
    assert Path(args[0]).name == ("neo4j.bat" if platform == "win32" else "neo4j")
    assert args[1:] == ["console"]
    assert options["env"]["NEO4J_CONF"] == str(home.data / "conf")
    assert options["env"]["JAVA_HOME"] == str(home.java_home())
    if platform != "win32":
        assert options["start_new_session"] is True  # stopped as one group
    assert json.loads((home.data / "run" / "graphme.json").read_text()) == {
        "pid": 4242, "port": 7688}


def test_the_one_running_for_this_home_is_used_and_nothing_started(tmp_path: Path) -> None:
    home = Neo4jHome(tmp_path)
    (home.data / "run").mkdir(parents=True)
    (home.data / "run" / "graphme.json").write_text(json.dumps({"pid": 1, "port": 7690}))

    uri = local_neo4j.ensure_local_neo4j(
        tmp_path, ("1g", "512m"), is_ours=lambda data, port: port == 7690,
        starting=lambda *a, **k: pytest.fail("started though one runs"))
    assert uri == "bolt://127.0.0.1:7690"


def test_one_answering_that_is_not_this_home_s_is_left_and_ours_started_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WSL's app, reached from Windows on loopback, answers too: its data folder is not ours.
    monkeypatch.setattr(local_neo4j, "_started", [])
    home = Neo4jHome(tmp_path)
    (home.data / "run").mkdir(parents=True)
    (home.data / "run" / "graphme.json").write_text(json.dumps({"pid": 1, "port": 7687}))
    began: list[int] = []

    def starting(home: Neo4jHome, data: Path, port: int, memory: Any) -> _Child:
        began.append(port)
        return _Child()

    uri = local_neo4j.ensure_local_neo4j(
        tmp_path, ("1g", "512m"), is_ours=lambda data, port: False,
        installing=lambda root: home, starting=starting,  # type: ignore[arg-type]
        free=lambda port: port != 7687, answers=lambda port: port == 7688)
    assert began == [7688] and uri == "bolt://127.0.0.1:7688"


def test_not_installed_it_is_fetched_first(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_neo4j, "_started", [])
    fetched: list[Path] = []

    def installing(root: Path) -> Neo4jHome:
        fetched.append(root)
        return Neo4jHome(root)

    local_neo4j.ensure_local_neo4j(
        tmp_path, ("1g", "512m"), installing=installing, is_ours=lambda d, p: False,
        starting=lambda *a, **k: _Child(), free=lambda p: True,  # type: ignore[arg-type,return-value]
        answers=lambda p: True)
    assert fetched == [tmp_path]


def test_one_that_dies_on_start_says_what_it_wrote(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_neo4j, "_started", [])
    home = Neo4jHome(tmp_path)
    (home.data / "logs").mkdir(parents=True)
    (home.data / "logs" / "console.log").write_text("Unsupported Java version")
    with pytest.raises(RuntimeError, match="Unsupported Java version"):
        local_neo4j.ensure_local_neo4j(
            tmp_path, ("1g", "512m"), installing=lambda root: home, is_ours=lambda d, p: False,
            starting=lambda *a, **k: _Child(ends=True), free=lambda p: True,  # type: ignore[arg-type,return-value]
            answers=lambda p: False, sleep=lambda s: None)


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_stopped_it_takes_the_server_its_launcher_started(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    ran: list[list[str]] = []
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(local_neo4j.processes, "run", lambda args, **k: ran.append(args) or
                        subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(local_neo4j.os, "killpg", lambda pid, sig: signalled.append((pid, sig)),
                        raising=False)
    child = _Child()
    local_neo4j.stop(child, platform=platform)  # type: ignore[arg-type]
    if platform == "win32":
        assert ran == [["taskkill", "/PID", "4242", "/T", "/F"]]
    else:
        assert signalled == [(4242, local_neo4j.signal.SIGTERM)]
    assert child.waited


def test_what_this_process_started_is_stopped_and_forgotten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped: list[object] = []
    home = Neo4jHome(tmp_path)
    (home.data / "run").mkdir(parents=True)
    (home.data / "run" / "graphme.json").write_text("{}")
    child = _Child()
    monkeypatch.setattr(local_neo4j, "_started", [(home, child)])
    monkeypatch.setattr(local_neo4j, "stop", stopped.append)
    local_neo4j.stop_started()
    assert stopped == [child] and not (home.data / "run" / "graphme.json").exists()
    local_neo4j.stop_started()
    assert stopped == [child]


def test_the_installer_s_fetch_never_fails_over_the_console_s_code_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # English Windows' console is cp1252: a Korean progress line failed the whole download.
    import io as _io
    import sys as _sys

    from graphme import neo4j_install

    console = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(_sys, "stdout", console)
    monkeypatch.setattr(neo4j_install, "__name__", "graphme.neo4j_install")
    said: list[str] = []
    monkeypatch.setattr(local_neo4j, "install",
                        lambda root, say: said.append("ok") or say("그래프 · 준비"))
    assert neo4j_install.main([str(tmp_path)]) == 0 and said == ["ok"]

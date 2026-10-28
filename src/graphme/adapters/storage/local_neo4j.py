"""The app's own Neo4j, without Docker.

Neo4j Community and a Java runtime are kept in the app's home (`neo4j/`, `jre/`), fetched from
their publishers and checked against their checksums -- by the installer, or on first use. The
graph lives beside them in `neo4j-data/`, so an update replaces the programs and keeps the
graph. It runs as a child of whatever needed it -- the app, a command -- on this machine's
loopback only, and is stopped when that ends. It asks for a password kept in the graph's folder
for this user alone: Bolt takes WebSocket connections too, so with none any page open in a
browser here could read or wipe the graph. A graph made before that is given one the first
time it is started or found running. Docker Desktop was dropped: with
any container running it runs dozens of CLI plugins every twelve seconds, and on Windows the PC
stalls each time.

A Neo4j answering on the port this home recorded is this home's only if it says this home's
data folder is its own: another install on the machine (WSL's, seen from Windows) answers on
loopback too.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import platform as platforms
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from graphme import processes

_log = logging.getLogger(__name__)

NEO4J_VERSION = "5.26.32"
JAVA_MAJOR = 21
FIRST_PORT = 7687
WAIT_SECONDS = 120
_starting = threading.Lock()
_started: list[tuple[Neo4jHome, subprocess.Popen[bytes]]] = []


@dataclass(frozen=True)
class Neo4jHome:
    """Where the app keeps Neo4j, its Java runtime and its graph."""

    root: Path
    graph: Path | None = None
    """The folder holding the graph (`neo4j-data/`): a workspace's own, beside the one install;
    None for the home's."""

    @property
    def neo4j(self) -> Path:
        return self.root / "neo4j"

    @property
    def jre(self) -> Path:
        return self.root / "jre"

    @property
    def graph_root(self) -> Path:
        return self.graph or self.root

    @property
    def data(self) -> Path:
        return self.graph_root / "neo4j-data"

    @property
    def password_file(self) -> Path:
        return self.data / "password"

    def java_home(self) -> Path:
        # macOS's runtime is a bundle: its home is inside.
        bundled = self.jre / "Contents" / "Home"
        return bundled if bundled.is_dir() else self.jre

    def installed(self) -> bool:
        marker = self.neo4j / ".graphme-version"
        return (marker.is_file() and marker.read_text(encoding="utf-8").strip() == NEO4J_VERSION
                and (self.java_home() / "bin").is_dir())

    def command(self, name: str, platform: str = sys.platform) -> list[str]:
        """`neo4j` or `neo4j-admin` as this system runs it."""
        script = self.neo4j / "bin" / (f"{name}.bat" if platform == "win32" else name)
        return [str(script)]

    def environment(self, conf: Path) -> dict[str, str]:
        return {**os.environ, "JAVA_HOME": str(self.java_home()), "NEO4J_CONF": str(conf),
                "NEO4J_HOME": str(self.neo4j)}


# Installing -----------------------------------------------------------------------------------


def _neo4j_archive(platform: str) -> str:
    kind = "windows.zip" if platform == "win32" else "unix.tar.gz"
    return f"https://dist.neo4j.org/neo4j-community-{NEO4J_VERSION}-{kind}"


def _jre_query(platform: str, machine: str) -> str:
    system = {"win32": "windows", "darwin": "mac"}.get(platform, "linux")
    arch = "aarch64" if machine.lower() in {"arm64", "aarch64"} else "x64"
    return (f"https://api.adoptium.net/v3/assets/latest/{JAVA_MAJOR}/hotspot"
            f"?image_type=jre&os={system}&architecture={arch}")


def _asked(url: str) -> urllib.request.Request:
    # Python's own User-Agent is turned away (403) by the publishers' servers.
    return urllib.request.Request(url, headers={"User-Agent": "graph-me-maybe"})


def _fetch(url: str, target: Path) -> None:
    with urllib.request.urlopen(_asked(url), timeout=60) as answer, target.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(answer, out, 1 << 20)


def _read(url: str) -> bytes:
    with urllib.request.urlopen(_asked(url), timeout=60) as answer:  # noqa: S310 -- fixed hosts
        data: bytes = answer.read()
        return data


def _digest(path: Path) -> str:
    hashed = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            hashed.update(block)
    return hashed.hexdigest()


def _unpack(archive: Path, into: Path) -> None:
    """`archive`'s one top folder becomes `into`, replacing what was there: unpacked beside it
    first, so a failure leaves the old one whole."""
    with tempfile.TemporaryDirectory(dir=into.parent) as scratch:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(scratch)  # noqa: S202 -- a publisher's archive, checksum-checked
        else:
            with tarfile.open(archive) as tarred:
                tarred.extractall(scratch, filter="tar")
        (top,) = Path(scratch).iterdir()
        if into.exists():
            shutil.rmtree(into)
        top.rename(into)


def install(
    root: Path, *, platform: str = sys.platform, machine: str | None = None,
    fetch: Callable[[str, Path], None] = _fetch, read: Callable[[str], bytes] = _read,
    say: Callable[[str], object] = print,
) -> Neo4jHome:
    """Neo4j Community and a Java runtime in `root`, each checked against its publisher's
    checksum. Nothing is fetched when this version is there already; the graph is never
    touched."""
    home = Neo4jHome(root)
    if home.installed():
        return home
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as scratch:
        work = Path(scratch)
        neo4j_url = _neo4j_archive(platform)
        neo4j_file = work / neo4j_url.rsplit("/", 1)[-1]
        say(f"fetching Neo4j {NEO4J_VERSION}")
        fetch(neo4j_url, neo4j_file)
        want = read(f"{neo4j_url}.sha256").decode().split()[0].lower()
        if _digest(neo4j_file) != want:
            raise RuntimeError(f"{neo4j_file.name}: checksum does not match")
        package = json.loads(read(_jre_query(platform, machine or platforms.machine())))[0][
            "binary"]["package"]
        jre_file = work / package["name"]
        say(f"fetching Java {JAVA_MAJOR}")
        fetch(package["link"], jre_file)
        if _digest(jre_file) != package["checksum"].lower():
            raise RuntimeError(f"{jre_file.name}: checksum does not match")
        _unpack(jre_file, home.jre)
        _unpack(neo4j_file, home.neo4j)
    (home.neo4j / ".graphme-version").write_text(NEO4J_VERSION, encoding="utf-8")
    say("the graph DB (Neo4j) is ready")
    return home


# Running --------------------------------------------------------------------------------------


# Bolt's opening: the magic number, then four versions offered (5.x, then 4.4). A port that
# only accepts is not one that answers: Neo4j picks a version back, four bytes.
_HANDSHAKE = bytes.fromhex("6060b017" "00000805" "00000404" "00000000" "00000000")


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(_HANDSHAKE)
            return len(connection.recv(4)) == 4
    except OSError:
        return False


def _free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


USER = "neo4j"


def password(root: Path) -> str | None:
    """The password this home's Neo4j asks for, or None for one that asks for none yet."""
    try:
        return Neo4jHome(root).password_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _keep(path: Path, secret: str) -> None:
    """`secret` written for this user alone (on Windows the folder's own rights hold)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(secret)


def _set_password(port: int, secret: str) -> None:
    """The password set on the Neo4j on `port`, which asks for none yet. Sent as a parameter,
    never on a command line."""
    from neo4j import GraphDatabase

    with GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=None) as driver:
        driver.execute_query(f"ALTER USER {USER} SET PASSWORD $secret CHANGE NOT REQUIRED",
                             secret=secret, database_="system")


def _given_password(home: Neo4jHome, port: int, secure: Callable[[int, str], None]) -> None:
    """A password kept, then set on the Neo4j on `port`. Kept first: a password set and then
    lost would lock the graph; one kept and not set is undone here, or by deleting the file."""
    secret = secrets.token_urlsafe(24)
    _keep(home.password_file, secret)
    try:
        secure(port, secret)
    except Exception:
        home.password_file.unlink(missing_ok=True)
        raise


def _data_folder(port: int, auth: tuple[str, str] | None = None) -> str | None:
    """The data folder the Neo4j on `port` says it keeps: who it belongs to."""
    from neo4j import GraphDatabase

    try:
        with GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=auth) as driver:
            records, _, _ = driver.execute_query(
                "SHOW SETTINGS YIELD name, value WHERE name = 'server.directories.data' "
                "RETURN value", database_="system")
            return str(records[0]["value"]) if records else None
    except Exception:  # noqa: BLE001 -- one with a password, or none: not ours
        return None


def conf(data: Path, port: int, memory: tuple[str, str],
         settings: Mapping[str, str] | None = None, *, auth: bool = False) -> str:
    heap, pagecache = memory
    lines: dict[str, object] = {
        "server.directories.data": data / "db",
        "server.directories.logs": data / "logs",
        "server.directories.run": data / "run",
        "server.directories.import": data / "import",
        "dbms.security.auth_enabled": "true" if auth else "false",
        "server.bolt.listen_address": f"127.0.0.1:{port}",
        "server.http.enabled": "false",
        "server.https.enabled": "false",
        "dbms.usage_report.enabled": "false",
        "server.memory.heap.max_size": heap,
        "server.memory.pagecache.size": pagecache,
        **(settings or {}),
    }
    # Forward slashes: Neo4j reads a backslash in its config as an escape.
    return "".join(f"{key}={str(value).replace(chr(92), '/')}\n" for key, value in lines.items())


def _record(data: Path) -> dict[str, int] | None:
    try:
        said = json.loads((data / "run" / "graphme.json").read_text(encoding="utf-8"))
        return {"pid": int(said["pid"]), "port": int(said["port"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _is_ours(data: Path, port: int) -> bool:
    if not _answers(port):
        return False
    secret = password(data.parent)
    said = _data_folder(port, (USER, secret) if secret else None)
    return said is not None and Path(said).resolve() == (data / "db").resolve()


def start(
    home: Neo4jHome, data: Path, port: int, memory: tuple[str, str], *,
    popen: Callable[..., subprocess.Popen[bytes]] = processes.popen,
    platform: str = sys.platform,
    settings: Mapping[str, str] | None = None,
    auth: bool = False,
) -> subprocess.Popen[bytes]:
    """Neo4j on `port` over the graph in `data`, a child of this process with no window."""
    for part in ("conf", "db", "logs", "run", "import"):
        (data / part).mkdir(parents=True, exist_ok=True)
    (data / "conf" / "neo4j.conf").write_text(conf(data, port, memory, settings, auth=auth),
                                              encoding="utf-8")
    log = (data / "logs" / "console.log").open("ab")
    options: dict[str, Any] = {"env": home.environment(data / "conf"), "stdout": log,
                               "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if platform != "win32":  # Windows' windowless start is `processes.popen`'s
        options["start_new_session"] = True  # one group: the launcher and the server it starts
        options["preexec_fn"] = _ending_unblocked
    try:
        child = popen([*home.command("neo4j", platform), "console"], **options)
    finally:
        log.close()  # the child has its own
    (data / "run" / "graphme.json").write_text(json.dumps({"pid": child.pid, "port": port}),
                                               encoding="utf-8")
    if platform != "win32" and popen is processes.popen:  # a real one: its group is its pid
        watch(child.pid, platform=platform)
    return child


_WATCHER = """
import os, signal, sys, time
parent, group = int(sys.argv[1]), int(sys.argv[2])
while os.getppid() == parent:
    time.sleep(1)
    try:
        os.killpg(group, 0)
    except OSError:
        sys.exit(0)  # stopped already, as it should be
try:
    os.killpg(group, signal.SIGTERM)
    for _ in range(60):
        time.sleep(1)
        os.killpg(group, 0)
    os.killpg(group, signal.SIGKILL)
except OSError:
    pass
"""


def _ending_unblocked() -> None:
    """Run in a child before its program: the signals that end it unblocked. The app blocks
    SIGTERM and SIGHUP in every thread but the one waiting for them, and a child inherits its
    parent's mask -- its Neo4j took no SIGTERM and ran until killed."""
    signal.pthread_sigmask(signal.SIG_SETMASK, set())


def watch(group: int, *, platform: str = sys.platform) -> None:
    """The Neo4j whose process group is `group` stopped if this process ends without stopping
    it -- killed, or a test run timed out: no atexit runs then, and a Neo4j in a session of its own
    ran on with nobody to stop it. A small process of its own watches for this one to go."""
    # ponytail: POSIX only; a Windows job object if a Neo4j left there turns up
    if platform == "win32":
        return
    processes.popen([sys.executable, "-c", _WATCHER, str(os.getpid()), str(group)],
                    start_new_session=True, preexec_fn=_ending_unblocked,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(child: subprocess.Popen[bytes], *, platform: str = sys.platform) -> None:
    """The Neo4j started as `child` stopped, with the server its launcher started."""
    try:
        if platform == "win32":
            # The launcher starts the server as a child of its own: the tree goes. (Neo4j
            # recovers from its transaction log on the next start.)
            processes.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                          capture_output=True, timeout=60)
        else:
            os.killpg(child.pid, signal.SIGTERM)  # a clean shutdown, launcher and server
        child.wait(timeout=60)  # the launcher ends once its server has
    except (OSError, subprocess.TimeoutExpired):
        pass


def came_up(
    port: int, child: subprocess.Popen[bytes], *, seconds: int = WAIT_SECONDS,
    answers: Callable[[int], bool] = _answers, sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Whether the Neo4j started as `child` answers on `port` within `seconds`; not once it
    has exited."""
    for _ in range(seconds):
        if answers(port):
            return True
        if child.poll() is not None:
            return False
        sleep(1)
    return False


def stop_started() -> None:
    """Every Neo4j this process started, stopped: the app's window closed, a command done."""
    while _started:
        home, child = _started.pop()
        stop(child)
        (home.data / "run" / "graphme.json").unlink(missing_ok=True)


def stop_graph(graph: Path) -> None:
    """The Neo4j this process started over the graph in `graph` stopped; the others run on."""
    for home, child in [(h, c) for h, c in _started if h.graph_root == graph]:
        _started.remove((home, child))
        stop(child)
        (home.data / "run" / "graphme.json").unlink(missing_ok=True)


def ensure_local_neo4j(
    root: Path, memory: tuple[str, str], *,
    graph: Path | None = None,
    installing: Callable[[Path], Neo4jHome] = install,
    starting: Callable[..., subprocess.Popen[bytes]] = start,
    is_ours: Callable[[Path, int], bool] = _is_ours,
    answers: Callable[[int], bool] = _answers,
    free: Callable[[int], bool] = _free,
    sleep: Callable[[float], None] = time.sleep,
    secure: Callable[[int, str], None] = _set_password,
) -> str:
    """The address of this home's Neo4j: the one running, else one started -- fetched first if
    it is not installed yet. Its password is `password(graph or root)`: `graph` is the folder of
    a workspace's own graph, run by the home's install."""
    with _starting:  # the page asks for the list and the map at once: one start
        home = Neo4jHome(root, graph)
        record = _record(home.data)
        if record is not None and is_ours(home.data, record["port"]):
            if password(home.graph_root) is None:  # asks for one from its next start
                _given_password(home, record["port"], secure)
            return f"bolt://127.0.0.1:{record['port']}"
        if not home.installed():
            home = replace(installing(root), graph=graph)
        port = next(p for p in range(FIRST_PORT, FIRST_PORT + 100) if free(p))
        _log.warning("starting the graph DB (Neo4j) on 127.0.0.1:%s", port)
        child = _up(home, port, memory, starting, answers, sleep)
        if password(home.graph_root) is None:
            _given_password(home, port, secure)
            stop(child)
            _started.remove((home, child))
            _up(home, port, memory, starting, answers, sleep)
        return f"bolt://127.0.0.1:{port}"


def _up(home: Neo4jHome, port: int, memory: tuple[str, str],
        starting: Callable[..., subprocess.Popen[bytes]], answers: Callable[[int], bool],
        sleep: Callable[[float], None]) -> subprocess.Popen[bytes]:
    """This home's Neo4j started on `port`, asking for its password if it has one."""
    child = starting(home, home.data, port, memory, auth=password(home.graph_root) is not None)
    if not _started:
        atexit.register(stop_started)
    _started.append((home, child))
    if came_up(port, child, answers=answers, sleep=sleep):
        return child
    said = (home.data / "logs" / "console.log")
    tail = said.read_text(encoding="utf-8", errors="replace")[-600:] if said.is_file() else ""
    raise RuntimeError(f"the graph DB (Neo4j) did not start: {tail.strip()}")

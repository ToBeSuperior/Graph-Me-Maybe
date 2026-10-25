"""The search models the app runs on this machine, and the server that runs them.

A model is one GGUF file in the app's home (`models/`), fetched from where its row names it and
kept only once its SHA-256 matches; removing it is deleting the file. It is served by llama.cpp's
own server (`llama-server`, fetched into `llama/` the same way, pinned to one build), a child
of this process on this machine's loopback only, at a port the OS assigns. Ollama was measured
first and dropped: its Windows package carries CUDA and is 1.5 GB, llama.cpp's CPU build 24 MB,
and an 8 GB laptop is the machine this is for.
"""
from __future__ import annotations

import atexit
import os
import platform as platforms
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from graphme import processes
from graphme.providers import ModelFile

Progress = Callable[[int, int], None]
"""Bytes fetched so far, and of how many."""
Fetch = Callable[[str, Path, Progress | None], None]

WAIT_SECONDS = 120
CONTEXT_TOKENS = 8192
"""The longest text the server takes in one piece; a passage is cut well below it."""


@dataclass(frozen=True)
class ServerBuild:
    """llama.cpp's server for one platform: its archive and the archive's SHA-256."""

    url: str
    sha256: str


LLAMA_BUILD = "b11871"
_RELEASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"
_BUILDS = {
    ("darwin", "arm64"): ("macos-arm64.tar.gz",
                          "389977376e89be4708e3e20c2f0c70b7252d15f5b848e754a26cf5fb06748d7c"),
    ("darwin", "x64"): ("macos-x64.tar.gz",
                        "f71341bfd7234e52b69c0717ab7e5f1a044d5df1afef5caa6e94ee21fc1cc40b"),
    ("linux", "x64"): ("ubuntu-x64.tar.gz",
                       "b0a29cc015275b751c0323875db007583b3bd1cb31e921087dd3cd3d37d5eef9"),
    ("win32", "x64"): ("win-cpu-x64.zip",
                       "a39ae0a534a3ccfbf5c10b5b5c662da93cecac3c796173de786b7e89f4f0ccfa"),
    ("win32", "arm64"): ("win-cpu-arm64.zip",
                         "83f8b73d1d14566246df2fbce90263c584e596ce8a62c45f0f26583e37b6c58e"),
}
"""Checksums as GitHub states them for the release's assets."""


def server_build(platform: str = sys.platform, machine: str | None = None) -> ServerBuild:
    """The pinned server build for this machine."""
    arch = "arm64" if (machine or platforms.machine()).lower() in {"arm64", "aarch64"} else "x64"
    system = platform if platform in {"darwin", "win32"} else "linux"
    found = _BUILDS.get((system, arch))
    if found is None:
        raise RuntimeError(f"no llama.cpp server build for {system} {arch}")
    name, digest = found
    return ServerBuild(url=f"{_RELEASE}/llama-{LLAMA_BUILD}-bin-{name}", sha256=digest)


def _fetch(url: str, target: Path, progress: Progress | None = None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "graph-me-maybe"})
    with urllib.request.urlopen(request, timeout=60) as answer, target.open("wb") as out:  # noqa: S310 -- pinned hosts, checksum-checked
        total, done = int(answer.headers.get("Content-Length") or 0), 0
        for block in iter(lambda: answer.read(1 << 20), b""):
            out.write(block)
            done += len(block)
            if progress is not None:
                progress(done, total)


def _digest(path: Path) -> str:
    hashed = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            hashed.update(block)
    return hashed.hexdigest()


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as answer:  # noqa: S310 -- loopback
            return bool(answer.status == 200)
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LocalModels:
    """The models in `root/models` and the servers running them for this process."""

    def __init__(
        self, root: Path, *, fetch: Fetch = _fetch, build: ServerBuild | None = None,
        popen: Callable[..., Any] = processes.popen,
        healthy: Callable[[int], bool] = _healthy, platform: str = sys.platform,
    ) -> None:
        self.root = root
        self._fetch = fetch
        self._build = build
        self._popen = popen
        self._healthy = healthy
        self._platform = platform
        self._lock = threading.Lock()
        self._running: dict[str, tuple[Any, str]] = {}
        """model file -> its server and address"""
        self._exit_hooked = False

    def path(self, model: ModelFile) -> Path:
        return self.root / "models" / model.file

    def installed(self, model: ModelFile) -> bool:
        path = self.path(model)
        return path.is_file() and path.stat().st_size == model.size

    def install(self, model: ModelFile, on_progress: Progress | None = None) -> None:
        """`model`'s file fetched and kept once its checksum holds; refused, nothing kept, when
        it does not."""
        if self.installed(model):
            return
        folder = self.path(model).parent
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / f".{model.file}.part"
        try:
            self._fetch(model.url, partial, on_progress)
            if _digest(partial) != model.sha256:
                raise RuntimeError(f"{model.file}: checksum does not match -- not installed")
            partial.replace(self.path(model))
        finally:
            partial.unlink(missing_ok=True)

    def remove(self, model: ModelFile) -> None:
        """`model`'s file deleted, its server stopped first."""
        self.stop(model)
        self.path(model).unlink(missing_ok=True)

    # Serving ----------------------------------------------------------------------------------

    def _server(self) -> Path:
        """llama.cpp's server, fetched for this platform and checked the first time."""
        folder = self.root / "llama"
        name = "llama-server.exe" if self._platform == "win32" else "llama-server"
        found = next(folder.rglob(name), None) if folder.is_dir() else None
        if found is not None and (folder / ".build").is_file() and \
                (folder / ".build").read_text(encoding="utf-8") == self._wanted().url:
            return found
        build = self._wanted()
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as scratch:
            archive = Path(scratch) / build.url.rsplit("/", 1)[-1]
            self._fetch(build.url, archive, None)
            if _digest(archive) != build.sha256:
                raise RuntimeError(f"{archive.name}: checksum does not match")
            unpacked = Path(scratch) / "unpacked"
            if archive.name.endswith(".zip"):
                with zipfile.ZipFile(archive) as zipped:
                    zipped.extractall(unpacked)  # noqa: S202 -- a pinned, checksum-checked archive
            else:
                with tarfile.open(archive) as tarred:
                    tarred.extractall(unpacked, filter="tar")
            if folder.exists():
                shutil.rmtree(folder)
            unpacked.rename(folder)
        (folder / ".build").write_text(build.url, encoding="utf-8")
        found = next(folder.rglob(name), None)
        if found is None:
            raise RuntimeError(f"{build.url}: no {name} in it")
        return found

    def _wanted(self) -> ServerBuild:
        return self._build or server_build(self._platform)

    def serve(self, model: ModelFile) -> str:
        """The OpenAI-compatible address (`/v1`) `model` is served at, its server started if
        it is not running."""
        with self._lock:
            running = self._running.get(model.file)
            if running is not None and running[0].poll() is None:
                return running[1]
            if not self.installed(model):
                raise LookupError(f"{model.file} is not installed")
            server, port = self._server(), _free_port()
            log = (self.root / "llama" / f"{Path(model.file).stem}.log").open("ab")
            options: dict[str, Any] = {"stdout": log, "stderr": subprocess.STDOUT,
                                       "stdin": subprocess.DEVNULL}
            if self._platform != "win32":
                options["start_new_session"] = True
            try:
                child = self._popen(
                    [str(server), "-m", str(self.path(model)), "--embedding",
                     "--host", "127.0.0.1", "--port", str(port),
                     "-c", str(CONTEXT_TOKENS), "-b", str(CONTEXT_TOKENS),
                     "-ub", str(CONTEXT_TOKENS)], **options)
            finally:
                log.close()
            for _ in range(WAIT_SECONDS):
                if self._healthy(port):
                    break
                if child.poll() is not None:
                    raise RuntimeError(f"the search model server stopped -- see {log.name}")
                time.sleep(1)
            else:
                child.terminate()
                raise RuntimeError(f"the search model server did not start -- see {log.name}")
            url = f"http://127.0.0.1:{port}/v1"
            self._running[model.file] = (child, url)
            if not self._exit_hooked:
                atexit.register(self.stop_all)
                self._exit_hooked = True
            return url

    def stop(self, model: ModelFile) -> None:
        with self._lock:
            running = self._running.pop(model.file, None)
        if running is not None:
            _end(running[0])

    def stop_all(self) -> None:
        with self._lock:
            running, self._running = list(self._running.values()), {}
        for child, _ in running:
            _end(child)


def _end(child: Any) -> None:
    try:
        child.terminate()
        child.wait(timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        if os.name != "nt":
            child.kill()

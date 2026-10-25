"""The search models the app runs on this machine: fetched into its home, checked against their
checksums, removed on request, and served by llama.cpp's server as a child. Invented files
only; nothing is fetched and no server is started."""
from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from graphme.adapters.local_models import LocalModels, ServerBuild
from graphme.providers import ModelFile

WEIGHTS = b"invented weights " * 64
MODEL = ModelFile(url="https://example.test/m.gguf", sha256=hashlib.sha256(WEIGHTS).hexdigest(),
                  size=len(WEIGHTS), file="m.gguf")


def _fetching(content: bytes, **others: bytes) -> Any:
    """What each address answers: `content` for the model, `others` by address."""
    def fetch(url: str, target: Path, progress: Any = None) -> None:
        body = others.get(url, content)
        target.write_bytes(body)
        if progress is not None:
            progress(len(body) // 2, len(body))
            progress(len(body), len(body))
    return fetch


ARCHIVE_URL = "https://example.test/llama.tar.gz"


def test_a_model_is_installed_once_its_checksum_holds_and_reports_how_far(tmp_path: Path) -> None:
    said: list[tuple[int, int]] = []
    models = LocalModels(tmp_path, fetch=_fetching(WEIGHTS))
    assert not models.installed(MODEL)

    models.install(MODEL, on_progress=lambda done, total: said.append((done, total)))

    assert models.installed(MODEL)
    assert (tmp_path / "models" / "m.gguf").read_bytes() == WEIGHTS
    assert said[-1] == (len(WEIGHTS), len(WEIGHTS)) and said[0][0] < said[-1][0]


def test_a_file_whose_checksum_differs_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    models = LocalModels(tmp_path, fetch=_fetching(b"tampered " * 10))

    with pytest.raises(RuntimeError, match="checksum"):
        models.install(MODEL)

    assert not models.installed(MODEL)
    assert list((tmp_path / "models").iterdir()) == []


def test_a_model_removed_is_no_longer_installed(tmp_path: Path) -> None:
    models = LocalModels(tmp_path, fetch=_fetching(WEIGHTS))
    models.install(MODEL)

    models.remove(MODEL)

    assert not models.installed(MODEL)


def _server_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        script = b"#!/bin/sh\n"
        info = tarfile.TarInfo("llama-test/llama-server")
        info.size, info.mode = len(script), 0o755
        archive.addfile(info, io.BytesIO(script))
    return buffer.getvalue()


class _Child:
    pid = 4242

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.stopped = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_a_model_is_served_on_loopback_by_the_server_fetched_for_this_platform(
    tmp_path: Path,
) -> None:
    archive = _server_archive()
    build = ServerBuild(url=ARCHIVE_URL, sha256=hashlib.sha256(archive).hexdigest())
    started: list[list[str]] = []

    def popen(args: list[str], **_: Any) -> _Child:
        started.append(args)
        return _Child()

    models = LocalModels(tmp_path, fetch=_fetching(WEIGHTS, **{ARCHIVE_URL: archive}),
                         build=build, popen=popen, healthy=lambda port: True,
                         platform="linux")
    models.install(MODEL)

    url = models.serve(MODEL)

    assert url.startswith("http://127.0.0.1:") and url.endswith("/v1")
    (args,) = started
    assert Path(args[0]).name == "llama-server"
    assert args[args.index("-m") + 1] == str(tmp_path / "models" / "m.gguf")
    assert "--embedding" in args and args[args.index("--host") + 1] == "127.0.0.1"
    assert models.serve(MODEL) == url and len(started) == 1  # already running


def test_a_model_not_installed_is_not_served(tmp_path: Path) -> None:
    models = LocalModels(tmp_path, fetch=_fetching(WEIGHTS), healthy=lambda port: True)

    with pytest.raises(LookupError, match="not installed"):
        models.serve(MODEL)


def test_removing_a_served_model_stops_its_server_first(tmp_path: Path) -> None:
    archive = _server_archive()
    build = ServerBuild(url=ARCHIVE_URL, sha256=hashlib.sha256(archive).hexdigest())
    child = _Child()
    models = LocalModels(tmp_path, fetch=_fetching(WEIGHTS, **{ARCHIVE_URL: archive}),
                         build=build, popen=lambda args, **_: child, healthy=lambda port: True,
                         platform="linux")
    models.install(MODEL)
    models.serve(MODEL)

    models.remove(MODEL)

    assert getattr(child, "stopped", False)
    assert not models.installed(MODEL)


def test_the_app_ending_stops_the_search_model_servers_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The window app ends with os._exit, which runs no exit handler.
    from graphme import launcher, runtime
    from graphme.adapters.storage import local_neo4j

    stopped: list[str] = []
    models = runtime.local_models(tmp_path)
    monkeypatch.setattr(models, "stop_all", lambda: stopped.append("models"))
    monkeypatch.setattr(local_neo4j, "stop_started", lambda: stopped.append("neo4j"))

    launcher._stop_neo4j()

    assert stopped == ["neo4j", "models"]

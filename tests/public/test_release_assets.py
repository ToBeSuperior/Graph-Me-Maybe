"""What the installers fetch, a pip install fetches itself on first use -- checked as they check."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from graphme import release_assets


def _zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


def _release(folder: Path, *, bad: str | None = None) -> Path:
    folder.mkdir(parents=True)
    assets = {"viewer.zip": _zip({"dist/index.js": b"v", "xlsx/a.js": b"x"}),
              "web.zip": _zip({"index.html": b"<html>", "assets/a.js": b"w"})}
    for name, data in assets.items():
        (folder / name).write_bytes(data)
    (folder / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(b'other' if name == bad else data).hexdigest()}  {name}\n"
        for name, data in assets.items()))
    return folder


def test_the_viewer_is_fetched_checked_and_unpacked(tmp_path: Path) -> None:
    source = _release(tmp_path / "release")
    said: list[str] = []
    got = release_assets.viewer(tmp_path / "viewer", source=str(source), say=said.append)
    assert (got / "dist" / "index.js").read_bytes() == b"v"
    assert any("viewer.zip" in line for line in said)


def test_a_checksum_that_does_not_match_unpacks_nothing(tmp_path: Path) -> None:
    source = _release(tmp_path / "release", bad="web.zip")
    with pytest.raises(RuntimeError, match="checksum"):
        release_assets.web(tmp_path / "web", source=str(source), say=lambda _: None)
    assert not (tmp_path / "web").exists()


def test_what_is_there_is_not_fetched_again(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("kept")
    got = release_assets.web(tmp_path / "web", source=str(tmp_path / "nowhere"),
                             say=lambda _: None)
    assert (got / "index.html").read_text() == "kept"


def test_the_release_of_this_version_is_asked_over_https(tmp_path: Path) -> None:
    files = _release(tmp_path / "release")
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        name = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, content=(files / name).read_bytes())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        release_assets.web(tmp_path / "web", client=client, say=lambda _: None)
    from graphme import __version__
    assert asked and all(f"/releases/download/v{__version__}/" in url for url in asked)
    assert (tmp_path / "web" / "index.html").is_file()


def test_setup_fetches_ahead_what_first_use_would(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    import graphme
    from graphme.config import load_settings

    source = _release(tmp_path / "release")
    monkeypatch.setenv("GRAPHME_INSTALL_SOURCE", str(source))
    monkeypatch.setenv("GRAPHME_HOME", str(tmp_path / "home"))
    settings = load_settings({"GRAPHME_PROFILE": "test"}).model_copy(update={
        "viewer_directory": tmp_path / "viewer", "chrome_executable": "chrome",
        "local_neo4j": None})

    graphme.setup(server=True, settings=settings, say=lambda _: None)

    assert (tmp_path / "viewer" / "dist").is_dir()
    assert (tmp_path / "home" / "web" / "index.html").is_file()

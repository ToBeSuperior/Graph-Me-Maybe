"""What the installers fetch, fetched by a pip install itself the first time it is needed: the
viewer bundle that draws DOCX, PPTX, XLSX and HWP, and the web app. Each comes from the GitHub
release of this version and is checked against that release's SHA256SUMS, as the installers do.
`GRAPHME_INSTALL_SOURCE` (theirs too) names another release: a URL, or a folder."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

REPO = "ToBeSuperior/Graph-Me-Maybe"

Say = Callable[[str], object]


def viewer(into: Path, *, source: str | None = None, client: httpx.Client | None = None,
           say: Say = print) -> Path:
    """The viewer bundle in `into`, fetched unless it is there."""
    return _fetched("viewer.zip", into, "dist", source=source, client=client, say=say)


def web(into: Path, *, source: str | None = None, client: httpx.Client | None = None,
        say: Say = print) -> Path:
    """The built web app in `into`, fetched unless it is there."""
    return _fetched("web.zip", into, "index.html", source=source, client=client, say=say)


def _source() -> str:
    from graphme import __version__

    return (os.environ.get("GRAPHME_INSTALL_SOURCE")
            or f"https://github.com/{REPO}/releases/download/v{__version__}")


def _fetched(name: str, into: Path, ready: str, *, source: str | None,
             client: httpx.Client | None, say: Say) -> Path:
    if (into / ready).exists():
        return into
    where = (source or _source()).rstrip("/")
    into.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="graphme-fetch-", dir=into.parent) as scratch:
        archive = Path(scratch) / name
        if where.startswith(("http://", "https://")):
            own = client is None
            http = client or httpx.Client(follow_redirects=True, timeout=60.0)
            try:
                sums = http.get(f"{where}/SHA256SUMS").raise_for_status().text
                with http.stream("GET", f"{where}/{name}") as answer:
                    answer.raise_for_status()
                    size = int(answer.headers.get("content-length") or 0)
                    say(f"fetching {name} ({size / 1e6:.0f} MB) from {where}" if size
                        else f"fetching {name} from {where}")
                    with archive.open("wb") as out:
                        for block in answer.iter_bytes():
                            out.write(block)
            finally:
                if own:
                    http.close()
        else:
            folder = Path(where)
            sums = (folder / "SHA256SUMS").read_text(encoding="utf-8")
            say(f"fetching {name} from {folder}")
            shutil.copyfile(folder / name, archive)
        expected = {line.split()[1]: line.split()[0] for line in sums.splitlines()
                    if len(line.split()) == 2}
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if expected.get(name) != digest:
            raise RuntimeError(f"{name} does not match its checksum in SHA256SUMS -- not unpacked")
        unpacked = Path(scratch) / "unpacked"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(unpacked)  # extractall keeps every member inside `unpacked`
        if into.is_dir() and not any(into.iterdir()):
            into.rmdir()
        elif into.exists():  # never deleted: it is not ours (a fetch lands whole or not at all)
            raise RuntimeError(f"{into} holds files but no {ready} -- empty it, or point "
                               "the setting at another folder")
        unpacked.replace(into)
    return into


def default_viewer() -> Path:
    """The viewer folder nobody named: the one this library fetches into."""
    return Path.home() / ".cache" / "graphme" / "ooxml-viewer"


_BROWSER_CHECKED = False


def ready_to_draw(viewer_directory: Path, chrome_executable: str | None, *,
                  say: Say = print) -> None:
    """Before a page is drawn: the viewer fetched into the default folder (a folder named in the
    settings is the owner's to fill), and Playwright's Chromium when no Chrome is installed --
    once a process; it returns at once when the browser is there."""
    global _BROWSER_CHECKED
    if viewer_directory == default_viewer():
        viewer(viewer_directory, say=say)
    if chrome_executable is None and not _BROWSER_CHECKED:
        import subprocess
        import sys

        from graphme import processes

        processes.run([sys.executable, "-m", "playwright", "install", "chromium", "--only-shell"],
                      check=False, stdout=subprocess.DEVNULL)
        _BROWSER_CHECKED = True

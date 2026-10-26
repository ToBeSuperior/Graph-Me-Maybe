"""Box Drive's folder on this machine: Box read with no sign-in of the app's own.

A company Box may refuse an app its admin has not approved; Box Drive, Box's own desktop client,
is usually allowed, and shows Box as an ordinary folder -- under `~/Library/CloudStorage/` on a
Mac (`Box-Box`), `%USERPROFILE%\\Box` on Windows. A file kept online only is fetched by Box Drive
when it is read. Folders are listed here a level at a time, every file as a folder shows it, and
nothing above the folder is.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from graphme.application.ingest import SUFFIX_FORMATS


def box_drive_root(home: Path, *, platform: str, environ: Mapping[str, str]) -> Path | None:
    """Where Box Drive shows Box on this machine; None when it is not here."""
    if platform == "win32":
        profile = environ.get("USERPROFILE")
        found = [Path(profile) / "Box"] if profile else []
    else:
        cloud = home / "Library" / "CloudStorage"
        found = sorted(cloud.glob("Box-*")) if cloud.is_dir() else []
        found.append(home / "Box")  # Box Drive before macOS's File Provider
    return next((path for path in found if path.is_dir()), None)


def _shown(path: Path) -> bool:
    return not path.name.startswith((".", "~$"))


def listing(root: Path, relative: str) -> dict[str, Any]:
    """The folder `relative` to `root`: where it is, its folders, then its documents. Each
    item's id is its path relative to `root`, written with `/`."""
    base = root.resolve()
    folder = (base / relative).resolve()
    if folder != base and base not in folder.parents:
        raise ValueError(f"{relative}: outside Box Drive's folder")
    if not folder.is_dir():
        raise FileNotFoundError(relative)
    items = sorted((p for p in folder.iterdir() if _shown(p)),
                   key=lambda p: (not p.is_dir(), p.name.lower()))
    above = folder.relative_to(base).parts
    return {
        "id": "/".join(above), "name": folder.name,
        "path": [{"id": "/".join(above[:i]), "name": (base.name if i == 0 else above[i - 1])}
                 for i in range(len(above))],
        "items": [_item(p, above) for p in items],
    }


def _item(path: Path, above: tuple[str, ...]) -> dict[str, Any]:
    """One entry as a folder shows it: a folder, or a file with its size and when it was
    changed -- readable when it is a document read here."""
    folder = path.is_dir()
    try:
        stat = path.stat()
        size, modified = (None if folder else stat.st_size), stat.st_mtime
    except OSError:
        size, modified = None, None
    return {"id": "/".join((*above, path.name)), "type": "folder" if folder else "file",
            "name": path.name, "size": size, "modified": modified,
            "readable": folder or path.suffix.lower() in SUFFIX_FORMATS}

"""A cloud drive's desktop client on this machine: the drive read with no sign-in of the app's own.

A company may refuse an app its admin has not approved; the drive's own desktop client is
usually allowed, and shows the drive as an ordinary folder. Box Drive is under
`~/Library/CloudStorage/` on a Mac (`Box-Box`), `%USERPROFILE%\\Box` on Windows; Google Drive for
desktop under `~/Library/CloudStorage/` too (`GoogleDrive-<account>`), and on Windows a drive
letter of its own (`G:`) -- or, mirrored, `%USERPROFILE%\\My Drive`. A file kept online only is
fetched by the client when it is read. Google's own documents (`.gdoc`, `.gsheet`) are links to
the web, not files, and are not read. Folders are listed here a level at a time, every file as
a folder shows it, and nothing above the folder is.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
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


MY_DRIVE = ("My Drive", "내 드라이브")
"""What Google Drive calls the user's own files, in English and Korean."""


def google_drive_root(home: Path, *, platform: str, environ: Mapping[str, str],
                      volumes: Iterable[Path] | None = None) -> Path | None:
    """Where Google Drive for desktop shows the drive on this machine; None when it is not
    here. Several accounts signed in: the first. `volumes` are Windows' drive letters."""
    # ponytail: the first account only; a pick among accounts if anyone signs in two
    if platform == "win32":
        letters = volumes if volumes is not None else (
            Path(f"{letter}:\\") for letter in "DEFGHIJKLMNOPQRSTUVWXYZ")
        found = [v for v in letters if any((v / name).is_dir() for name in MY_DRIVE)]
        profile = environ.get("USERPROFILE")
        found += [Path(profile) / name for name in MY_DRIVE] if profile else []
    else:
        cloud = home / "Library" / "CloudStorage"
        found = sorted(cloud.glob("GoogleDrive-*")) if cloud.is_dir() else []
    return next((path for path in found if path.is_dir()), None)


DESKTOP_DRIVES = {"box": box_drive_root, "google": google_drive_root}
"""Each drive whose desktop client is read, by the name the app gives it."""


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
        "id": "/".join(above), "name": folder.name or str(folder),
        "path": [{"id": "/".join(above[:i]), "name": (base.name or str(base)) if i == 0
                  else above[i - 1]}
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


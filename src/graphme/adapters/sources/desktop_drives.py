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

import json
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Drive:
    """What a drive's desktop client shows: the folder it is in, and -- when that folder holds
    others' too -- the drive's own folders in it, the only ones listed."""

    base: Path
    top: tuple[str, ...] | None = None


def onedrive_drive(home: Path, *, platform: str, environ: Mapping[str, str],
                   mounts: list[str] | None = None) -> Drive | None:
    """Where OneDrive shows the user's own OneDrive and the SharePoint libraries synced to this
    machine; None when it is not here. On a Mac these are `OneDrive-<org>` and
    `OneDrive-SharedLibraries-<org>` beside other drives' folders under CloudStorage; on Windows
    wherever OneDrive says it mounted them (`mounts`, read from the registry when not given) --
    `OneDrive - <org>` and `<org>\\<site> - <library>` beside everything else in the profile."""
    if platform != "win32":
        cloud = home / "Library" / "CloudStorage"
        top = sorted(p.name for p in cloud.glob("OneDrive-*") if p.is_dir()) \
            if cloud.is_dir() else []
        return Drive(cloud, tuple(top)) if top else None
    return _spread([Path(m) for m in (mounts if mounts is not None else _onedrive_mounts(environ))])


def _spread(places: list[Path]) -> Drive | None:
    """Several folders of one drive (accounts, libraries) shown from the folder above them."""
    places = [p for p in places if p.is_dir()]
    if not places:
        return None
    try:
        base = Path(os.path.commonpath(places))
    except ValueError:  # on two drive letters: the first one's
        base = places[0].parent
    if base in places:  # one place, or one inside another: shown from above it
        base = base.parent
    return Drive(base, tuple(sorted({p.relative_to(base).parts[0] for p in places})))


def dropbox_drive(home: Path, *, platform: str, environ: Mapping[str, str]) -> Drive | None:
    """Where Dropbox keeps the user's Dropbox, and a team's beside it; None when it is not here.
    Dropbox writes where in `info.json` (`personal`, `business`); without one, where it
    installs: `~/Library/CloudStorage/Dropbox*` on a Mac, `~/Dropbox` elsewhere."""
    infos = [Path(environ[name]) / "Dropbox" / "info.json" for name in ("APPDATA", "LOCALAPPDATA")
             if environ.get(name)] if platform == "win32" else [home / ".dropbox" / "info.json"]
    places: list[Path] = []
    for info in infos:
        try:
            said = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        places += [Path(said[k]["path"]) for k in ("personal", "business")
                   if isinstance(said.get(k), dict) and said[k].get("path")]
    if places := [p for p in dict.fromkeys(places) if p.is_dir()]:
        return Drive(places[0]) if len(places) == 1 else _spread(places)
    cloud = home / "Library" / "CloudStorage"
    found = sorted(cloud.glob("Dropbox*")) if platform != "win32" and cloud.is_dir() else []
    found.append(home / "Dropbox")
    return next((Drive(p) for p in found if p.is_dir()), None)


def icloud_drive_root(home: Path, *, platform: str, environ: Mapping[str, str]) -> Path | None:
    """Where iCloud Drive keeps the user's files; None when it is not here."""
    if platform == "win32":
        profile = environ.get("USERPROFILE")
        found = [Path(profile) / "iCloudDrive"] if profile else []
    else:
        found = [home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"]
    return next((path for path in found if path.is_dir()), None)


def synology_drive_root(home: Path, *, platform: str,
                        environ: Mapping[str, str]) -> Path | None:
    """Where Synology Drive Client syncs a NAS's folders; None when it is not here."""
    # shortcut: the client's default folder only; read its own settings if users move it often
    if platform == "win32":
        profile = environ.get("USERPROFILE")
        found = [Path(profile) / "SynologyDrive"] if profile else []
    else:
        cloud = home / "Library" / "CloudStorage"
        found = sorted(cloud.glob("SynologyDrive-*")) if cloud.is_dir() else []
        found.append(home / "SynologyDrive")
    return next((path for path in found if path.is_dir()), None)


def _onedrive_mounts(environ: Mapping[str, str]) -> list[str]:
    """Where OneDrive on Windows mounted each OneDrive and SharePoint library it syncs."""
    found = [environ[name] for name in ("OneDriveCommercial", "OneDriveConsumer") if
             environ.get(name)]
    if sys.platform != "win32":  # the registry is Windows' alone
        return found
    try:
        import winreg

        providers = r"Software\SyncEngines\Providers\OneDrive"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, providers) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                with winreg.OpenKey(key, winreg.EnumKey(key, index)) as provider:
                    found.append(str(winreg.QueryValueEx(provider, "MountPoint")[0]))
    except OSError:
        pass
    return list(dict.fromkeys(found))


def _whole(find: Callable[..., Path | None]) -> Callable[..., Drive | None]:
    def found(home: Path, **where: Any) -> Drive | None:
        path = find(home, **where)
        return Drive(path) if path is not None else None
    return found


DESKTOP_DRIVES: dict[str, Callable[..., Drive | None]] = {
    "box": _whole(box_drive_root), "google": _whole(google_drive_root), "onedrive": onedrive_drive,
    "dropbox": dropbox_drive, "icloud": _whole(icloud_drive_root),
    "synology": _whole(synology_drive_root)}
"""Each drive whose desktop client is read, by the name the app gives it."""


def _shown(path: Path) -> bool:
    return not path.name.startswith((".", "~$"))


def listing(root: Path, relative: str, top: tuple[str, ...] | None = None) -> dict[str, Any]:
    """The folder `relative` to `root`: where it is, its folders, then its documents. Each
    item's id is its path relative to `root`, written with `/`. With `top`, only those folders
    of `root` are listed, and nothing outside them."""
    base = root.resolve()
    folder = (base / relative).resolve()
    if folder != base and base not in folder.parents:
        raise ValueError(f"{relative}: outside the drive's folder")
    if top is not None and folder != base and folder.relative_to(base).parts[0] not in top:
        raise ValueError(f"{relative}: outside the drive's folder")
    if not folder.is_dir():
        raise FileNotFoundError(relative)
    items = sorted((p for p in folder.iterdir()
                    if _shown(p) and (top is None or folder != base or p.name in top)),
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



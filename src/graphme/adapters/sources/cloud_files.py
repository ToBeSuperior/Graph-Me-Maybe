"""A cloud drive's files on this machine: kept online only, or downloaded and kept here.

A desktop drive (Box Drive, Google Drive for desktop, OneDrive) shows every file, but one kept
online only takes no room until it is read -- and once read, the drive keeps it. A build reads
thousands, so what was online only before is given back to the cloud once read (`evict`), and
what each drive keeps here is measured (`local_usage`). A folder never opened is online only too,
and nothing in it is here: it is not walked, so measuring never asks the cloud for a listing.
"""
from __future__ import annotations

import contextlib
import os
import stat
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from graphme import processes

DATALESS = 0x40000000
"""macOS's `SF_DATALESS`: the file or folder is a placeholder, its contents in the cloud."""
WINDOWS_IN_THE_CLOUD = 0x1000 | 0x40000 | 0x400000
"""Windows' OFFLINE, RECALL_ON_OPEN and RECALL_ON_DATA_ACCESS: nothing of it is here yet."""


def online_only(path: Path) -> bool:
    """Whether `path` is kept in the cloud only, with nothing of it on this machine."""
    try:
        found = os.lstat(path)
    except OSError:
        return False
    if sys.platform == "win32":
        return bool(getattr(found, "st_file_attributes", 0) & WINDOWS_IN_THE_CLOUD)
    return bool(getattr(found, "st_flags", 0) & DATALESS)


def evict(paths: Iterable[Path]) -> int:
    """`paths` given back to the cloud: their contents removed from this machine, the files
    kept as online-only placeholders. How many were; one the drive refuses is left as it is."""
    given = 0
    for path in paths:
        with contextlib.suppress(Exception):  # a drive that cannot: the file stays here
            if _evict(path):
                given += 1
    return given


def _evict(path: Path) -> bool:
    if sys.platform == "darwin":
        import Foundation  # type: ignore[import-untyped,import-not-found,unused-ignore]

        url = Foundation.NSURL.fileURLWithPath_(str(path))
        done, _ = Foundation.NSFileManager.defaultManager().evictUbiquitousItemAtURL_error_(
            url, None)
        return bool(done)
    if sys.platform == "win32":  # OneDrive's Files On-Demand: unpinned, then freed
        said = processes.run(["attrib", "+U", "-P", str(path)], capture_output=True, timeout=60)
        return said.returncode == 0
    return False


def _size(path: Path) -> int:
    found = path.stat()
    blocks = getattr(found, "st_blocks", None)  # what it takes on disk, not what it says
    return blocks * 512 if blocks is not None and sys.platform != "win32" else found.st_size


def local_usage(root: Path, top: tuple[str, ...] | None, *,
                online_only: Callable[[Path], bool] = online_only,
                size: Callable[[Path], int] = _size) -> dict[str, int]:
    """What the drive under `root` keeps on this machine (only its `top` folders, when given):
    its bytes and how many files. Hidden files and folders never opened are passed over."""
    used = files = 0
    stack = [root / name for name in top] if top is not None else [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            path = Path(entry.path)
            try:
                kind = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if online_only(path):
                continue
            if stat.S_ISDIR(kind):
                stack.append(path)
            elif stat.S_ISREG(kind):
                with contextlib.suppress(OSError):
                    used += size(path)
                    files += 1
    return {"bytes": used, "files": files}

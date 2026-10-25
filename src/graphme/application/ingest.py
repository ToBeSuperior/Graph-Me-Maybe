from __future__ import annotations

import errno
import os
import time
from collections.abc import Callable
from pathlib import Path

from graphme.domain.enums import DocumentFormat

SUFFIX_FORMATS = {
    ".xlsx": DocumentFormat.XLSX,
    ".pptx": DocumentFormat.PPTX,
    ".docx": DocumentFormat.DOCX,
    ".pdf": DocumentFormat.PDF,
}
"""The files read here, by extension."""


def format_of(source: Path) -> DocumentFormat:
    """The format a file is read as, by its extension."""
    fmt = SUFFIX_FORMATS.get(source.suffix.lower())
    if fmt is None:
        raise ValueError(f"unsupported document format: {source.suffix!r}")
    return fmt


RETRY_DELAYS = (10.0, 30.0)
"""The waits before a folder that timed out listing is asked again. A cloud drive's folder (Box
Drive, Google Drive for desktop) is listed by asking the cloud, and Box answers "too many
requests" (429) or "unavailable" (503) for a while when thousands are asked: the drive waits
and asks again, and the system gives up on the listing (ETIMEDOUT) before it answers."""
REST_SECONDS = 60.0
"""How long the walk rests before the folders that ran out of tries are asked once more."""
PACE_SECONDS = 1.0
"""Once the source has timed out, the wait before each folder is listed: asking more slowly
is what lets a throttled drive answer. A source that answers is never waited for."""


def documents_in(
    folder: Path, *, skipped: list[Path] | None = None,
    on_found: Callable[[int], None] | None = None,
) -> list[Path]:
    """Every document under a folder that can be read, in a stable order.

    A folder is how a corpus arrives: everything in it, subfolders too. What is not one of
    the formats read here is passed over, and so is what is not the user's -- a lock file
    Office keeps beside an open document ("~$..."), anything hidden. A subfolder that cannot be
    listed is asked again after `RETRY_DELAYS`, and once more after the rest is listed; one
    that still cannot is passed over into `skipped` when one is given, and raised when not.
    `on_found` hears how many have been found so far, as each is.
    """
    found: list[Path] = []
    strained = [False]
    """Whether the source has timed out: from then on, folders are asked for slowly."""

    def walk(waiting: list[Path], late: list[Path] | None) -> None:
        while waiting:
            here = waiting.pop()
            if strained[0]:
                time.sleep(PACE_SECONDS)
            try:
                entries = _listed(here, strained)
            except OSError as error:
                if late is not None and error.errno == errno.ETIMEDOUT:
                    late.append(here)  # asked again once the rest is listed
                elif skipped is None:
                    raise
                else:
                    skipped.append(here)
                continue
            for entry in entries:
                if entry.name.startswith((".", "~$")):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        waiting.append(Path(entry.path))
                    elif entry.is_file() and Path(entry.name).suffix.lower() in SUFFIX_FORMATS:
                        found.append(Path(entry.path))
                        if on_found is not None:
                            on_found(len(found))
                except OSError:
                    continue  # gone, or not this user's to read

    late: list[Path] = []
    walk([folder], late)
    if late:
        time.sleep(REST_SECONDS)
        walk(late, None)
    return sorted(found)


def _listed(folder: Path, strained: list[bool]) -> list[os.DirEntry[str]]:
    for wait in (*RETRY_DELAYS, None):
        try:
            with os.scandir(folder) as entries:
                return list(entries)
        except OSError as error:
            if error.errno != errno.ETIMEDOUT or wait is None:
                raise
            strained[0] = True
            time.sleep(wait)
    return []


def documents_at(
    path: Path, *, skipped: list[Path] | None = None,
    on_found: Callable[[int], None] | None = None,
) -> list[Path]:
    """What a path holds to be read: a folder's documents (`documents_in`), or one file --
    refused here, before anything is read, if it is not a format read here."""
    if path.is_dir():
        return documents_in(path, skipped=skipped, on_found=on_found)
    if not path.is_file():
        raise FileNotFoundError(path)
    format_of(path)
    return [path]

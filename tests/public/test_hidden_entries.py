"""What Windows marks hidden or system ($RECYCLE.BIN, System Volume Information), and what a Mac
hides (Finder's folder icon `Icon\\r`, anything flagged hidden), is not the user's: a drive's
folder list, its usage and a build all pass it over, as they pass dot files."""
from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from graphme.adapters.sources.desktop_drives import listing
from graphme.application.ingest import documents_at

windows = pytest.mark.skipif(sys.platform != "win32", reason="file attributes are Windows'")
mac = pytest.mark.skipif(sys.platform != "darwin", reason="the hidden flag is a Mac's")


def _hide(path: Path) -> None:
    import ctypes

    attributes = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
    assert ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes)  # type: ignore[attr-defined]


@windows
def test_a_hidden_system_folder_is_not_listed_or_read(tmp_path: Path) -> None:
    bin_ = tmp_path / "$RECYCLE.BIN"
    bin_.mkdir()
    (bin_ / "gone.pdf").write_bytes(b"%PDF-1.4 invented")
    (tmp_path / "kept.pdf").write_bytes(b"%PDF-1.4 invented")
    _hide(bin_)

    assert [i["name"] for i in listing(tmp_path, "")["items"]] == ["kept.pdf"]
    assert [p.name for p in documents_at(tmp_path)] == ["kept.pdf"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows takes no carriage return in a name")
def test_finders_folder_icon_is_not_listed_or_read(tmp_path: Path) -> None:
    # A folder given a custom icon holds a file named "Icon" and a carriage return; a cloud
    # drive syncs it like any other, and listed it was shown as a file.
    (tmp_path / "Icon\r").write_bytes(b"")
    (tmp_path / "kept.pdf").write_bytes(b"%PDF-1.4 invented")

    assert [i["name"] for i in listing(tmp_path, "")["items"]] == ["kept.pdf"]


@mac
def test_what_a_mac_flags_hidden_is_not_listed_or_read(tmp_path: Path) -> None:
    import os

    hidden = tmp_path / "Invented hidden.pdf"
    hidden.write_bytes(b"%PDF-1.4 invented")
    (tmp_path / "kept.pdf").write_bytes(b"%PDF-1.4 invented")
    os.chflags(hidden, stat.UF_HIDDEN)

    assert [i["name"] for i in listing(tmp_path, "")["items"]] == ["kept.pdf"]
    assert [p.name for p in documents_at(tmp_path)] == ["kept.pdf"]

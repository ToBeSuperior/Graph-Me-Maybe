"""What Windows marks hidden or system ($RECYCLE.BIN, System Volume Information) is not the
user's: a drive's folder list, its usage and a build all pass it over, as they pass dot files."""
from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from graphme.adapters.sources.desktop_drives import listing
from graphme.application.ingest import documents_at

windows = pytest.mark.skipif(sys.platform != "win32", reason="file attributes are Windows'")


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

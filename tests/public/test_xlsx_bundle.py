"""The bundle's copy of a workbook is rewritten in place, which Windows allows only once
nothing holds the file open. Invented data."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from graphme.application import xlsx_bundle
from graphme.application.xlsx_bundle import XlsxBundleOrchestrator


def test_a_prepared_copy_is_replaced_only_once_it_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_now: set[Path] = set()

    class TrackedZip(zipfile.ZipFile):
        def __init__(self, file: Path, *args: object, **kwargs: object) -> None:
            super().__init__(file, *args, **kwargs)  # type: ignore[arg-type]
            self.tracked = Path(file).resolve() if isinstance(file, str | os.PathLike) else None
            if self.tracked is not None:
                open_now.add(self.tracked)

        def close(self) -> None:
            if self.tracked is not None:
                open_now.discard(self.tracked)
            super().close()

    real_replace = os.replace

    def windows_replace(source: Path, target: Path) -> None:
        if Path(target).resolve() in open_now:  # what Windows answers for an open file
            raise PermissionError(13, "Access is denied")
        real_replace(source, target)

    monkeypatch.setattr(xlsx_bundle.zipfile, "ZipFile", TrackedZip)
    monkeypatch.setattr(xlsx_bundle.os, "replace", windows_replace)
    book = Workbook()
    book.active["A1"] = "invented"
    copy = tmp_path / "invented.xlsx"
    book.save(copy)

    areas = XlsxBundleOrchestrator._prepare_copy(copy)

    assert list(areas) == ["Sheet"]
    assert not list(tmp_path.glob(".invented.xlsx.*.tmp"))

"""A folder walked for its documents when some of it answers slowly: a cloud drive's folder
(Box Drive) is listed by asking the cloud, and one listing can time out. Invented folders."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

import pytest

from graphme.application import ingest
from graphme.application.ingest import documents_at


def _tree(root: Path) -> Path:
    for folder in ("a", "b", "b/deep"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "a" / "one.pdf").write_bytes(b"%PDF")
    (root / "b" / "two.docx").write_bytes(b"docx")
    (root / "b" / "deep" / "three.xlsx").write_bytes(b"xlsx")
    (root / "b" / "notes.txt").write_text("not a document")
    return root


def _slow(monkeypatch: pytest.MonkeyPatch, where: Path, times: int) -> list[str]:
    """`where` times out `times` times before it lists; every listing is recorded."""
    listed: list[str] = []
    real = os.scandir
    left = [times]

    def scandir(path: Any) -> Any:
        listed.append(str(path))
        if Path(path) == where and left[0] != 0:
            left[0] -= 1
            raise OSError(errno.ETIMEDOUT, "Operation timed out", str(path))
        return real(path)

    monkeypatch.setattr(ingest.os, "scandir", scandir)
    monkeypatch.setattr(ingest, "RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(ingest, "REST_SECONDS", 0.0)
    monkeypatch.setattr(ingest, "PACE_SECONDS", 0.0)
    return listed


def test_a_folder_that_times_out_once_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "Box")
    _slow(monkeypatch, root / "b", times=1)
    skipped: list[Path] = []

    found = documents_at(root, skipped=skipped)

    assert [p.name for p in found] == ["one.pdf", "three.xlsx", "two.docx"]
    assert skipped == []


def test_a_folder_that_never_lists_is_passed_over_and_the_rest_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "Box")
    _slow(monkeypatch, root / "b", times=-1)  # never
    skipped: list[Path] = []

    found = documents_at(root, skipped=skipped)

    assert [p.name for p in found] == ["one.pdf"]
    assert skipped == [root / "b"]


def test_each_document_found_is_counted_as_it_is_found(tmp_path: Path) -> None:
    root = _tree(tmp_path / "Box")
    counted: list[int] = []

    documents_at(root, on_found=counted.append)

    assert counted == [1, 2, 3]


def test_without_a_record_of_what_was_passed_over_a_timeout_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "Box")
    _slow(monkeypatch, root / "b", times=-1)

    with pytest.raises(OSError):
        documents_at(root)


def test_a_folder_still_timing_out_is_asked_once_more_after_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Box answers "too many requests" for minutes at a time: a folder that ran out of tries
    # while it did is asked again once everything else is listed.
    root = _tree(tmp_path / "Box")
    _slow(monkeypatch, root / "b", times=3)  # every try of the first pass
    skipped: list[Path] = []

    found = documents_at(root, skipped=skipped)

    assert [p.name for p in found] == ["one.pdf", "three.xlsx", "two.docx"]
    assert skipped == []


def test_once_the_source_times_out_folders_are_listed_slower(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "Box")
    _slow(monkeypatch, root / "b", times=1)
    monkeypatch.setattr(ingest, "PACE_SECONDS", 7.0)
    waits: list[float] = []
    monkeypatch.setattr(ingest.time, "sleep", waits.append)

    documents_at(root, skipped=[])

    assert 7.0 in waits  # spaced out after the time-out


def test_a_folder_that_answers_is_listed_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "Box")
    waits: list[float] = []
    monkeypatch.setattr(ingest.time, "sleep", waits.append)

    documents_at(root, skipped=[])

    assert waits == []

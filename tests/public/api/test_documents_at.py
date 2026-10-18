"""A corpus arrives as a folder or as one file."""
from __future__ import annotations

from pathlib import Path

import pytest

from graphme.application.ingest import documents_at


def test_a_folder_is_every_document_in_it(tmp_path: Path) -> None:
    (tmp_path / "a.docx").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    assert documents_at(tmp_path) == [tmp_path / "a.docx"]


def test_a_file_is_itself(tmp_path: Path) -> None:
    one = tmp_path / "b.pptx"
    one.write_bytes(b"x")
    assert documents_at(one) == [one]


def test_a_file_of_another_kind_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported"):
        documents_at(other)


def test_nothing_there_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        documents_at(tmp_path / "missing.docx")

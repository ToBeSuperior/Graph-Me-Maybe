"""The import bundle's scripts end their lines with LF wherever they are written: `import.sh`
is a POSIX shell script. Invented records only."""
from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from graphme.adapters.export.neo4j_import import write_import_bundle
from tests.fakes.corpus import rows_of


def test_written_on_windows_the_scripts_still_end_lines_with_lf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows' text mode wrote `set -e\r`, and sh refused it
    # ("set: illegal option -"): every .dump made on Windows failed.
    written = pathlib.Path.write_text

    def as_windows(self, data, encoding=None, errors=None, newline=None):  # noqa: ANN001
        if newline is None:
            data = data.replace("\n", "\r\n")
        return written(self, data, encoding=encoding, errors=errors, newline=newline or "")

    monkeypatch.setattr(pathlib.Path, "write_text", as_windows)
    records = {"nodes": [{"id": "a", "labels": ["DocNode", "Document"],
                          "properties": {"id": "a"}}], "relationships": []}
    write_import_bundle(rows_of(records), tmp_path / "bundle")

    for name in ("import.sh", "schema.cypher"):
        assert b"\r" not in (tmp_path / "bundle" / name).read_bytes(), name

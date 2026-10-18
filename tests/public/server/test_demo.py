"""The demo's additions, from the package: the samples route and its settings."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from graphme.samples import write_samples
from graphme.server.demo import demo_settings


def test_the_samples_are_written_the_same_each_time(tmp_path: Path) -> None:
    first = {path.name: path.read_bytes() for path in write_samples(tmp_path / "a")}
    second = {path.name: path.read_bytes() for path in write_samples(tmp_path / "b")}
    assert first == second and len(first) == 10


def test_the_demo_reads_twice_unless_told() -> None:
    assert demo_settings({"GRAPHME_PROFILE": "test"}).hierarchy_readings == 2
    told = {"GRAPHME_PROFILE": "test", "GRAPHME_HIERARCHY_READINGS": "1"}
    assert demo_settings(told).hierarchy_readings == 1


def test_the_samples_command_lists_its_files_on_a_console_that_has_no_hangul(
    tmp_path: Path,
) -> None:
    # A Windows pipe writes in the ANSI code page (cp1252 in English), which has no Hangul.
    done = subprocess.run([sys.executable, "-m", "graphme.samples", str(tmp_path)],
                          capture_output=True, timeout=120,
                          env={**os.environ, "PYTHONIOENCODING": "cp1252"})

    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert len(done.stdout.splitlines()) == len(list(tmp_path.iterdir()))

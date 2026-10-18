"""Each example says how it is run, with no key, no Neo4j and no document."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.parametrize(("runner", "example"), [
    (sys.executable, "read_document.py"),
    (sys.executable, "build_and_ask.py"),
    # Windows finds WSL's bash launcher first, which runs nothing without a Linux installed.
    pytest.param("bash", "http_ask.sh", marks=pytest.mark.skipif(
        sys.platform == "win32", reason="bash on Windows is the WSL launcher")),
])
def test_each_example_says_how_it_is_run(runner: str, example: str) -> None:
    done = subprocess.run([runner, str(EXAMPLES / example), "--help"],
                          capture_output=True, text=True, timeout=60)

    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout

"""Every public test runs against a home of its own: a command started where there is no `.env`
reads the installed app's settings, and an app installed on the machine running the tests made
them read its provider and keys."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _a_home_of_its_own(tmp_path_factory: pytest.TempPathFactory,
                       monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path_factory.mktemp("graphme-home")
    monkeypatch.setenv("GRAPHME_HOME", str(home))
    return home

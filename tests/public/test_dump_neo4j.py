"""A `.dump` is made though the scratch Neo4j is stopped by force: run with GRAPHME_NEO4J_TEST=1.

Windows stops it with `taskkill /T /F`, and `neo4j-admin database dump` refused the database
it left ("Active logical log detected"). The same stop is made here with SIGKILL. Invented
document only."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from graphme.adapters.export.neo4j import graph_records
from graphme.adapters.export.neo4j_admin import LocalNeo4jAdmin
from graphme.adapters.export.neo4j_import import write_import_bundle
from graphme.adapters.storage import local_neo4j
from graphme.config import app_home
from tests.fakes.corpus import rows_of
from tests.fakes.documents import named_document

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHME_NEO4J_TEST") != "1" or sys.platform == "win32",
    reason="needs the local Neo4j; Windows stops it by force already",
)


def test_a_dump_is_made_though_the_scratch_neo4j_is_stopped_by_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forced(child, **_: object) -> None:  # noqa: ANN001 -- what taskkill /T /F does
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=60)

    monkeypatch.setattr(local_neo4j, "stop", forced)
    write_import_bundle(rows_of(graph_records(named_document("a", "invented.pdf",
                                                             title="Invented"))),
                        tmp_path / "bundle")

    LocalNeo4jAdmin(local_neo4j.install(app_home())).dump(tmp_path / "bundle",
                                                          tmp_path / "graph.dump")

    assert (tmp_path / "graph.dump").stat().st_size > 0

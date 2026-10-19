"""`.dump` and the Aura upload from the app's own `neo4j-admin`, without Docker. Invented
records; `neo4j-admin` and the scratch Neo4j are fakes."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from graphme.adapters.export.neo4j_admin import LocalNeo4jAdmin
from graphme.adapters.export.neo4j_import import write_import_bundle
from graphme.adapters.storage.local_neo4j import Neo4jHome
from tests.fakes.corpus import rows_of

RECORDS = {
    "nodes": [{"id": "a", "labels": ["DocNode", "Document"], "properties": {"id": "a"}},
              {"id": "b", "labels": ["DocNode", "Document"], "properties": {"id": "b"}}],
    "relationships": [{"id": "r", "type": "SAME_AS", "start": "a", "end": "b",
                       "properties": {}}],
}


class _Admin:
    """`neo4j-admin`: what it was asked, a dump written where it was told."""

    def __init__(self, fails: str | None = None) -> None:
        self.asked: list[tuple[list[str], dict[str, Any]]] = []
        self.fails = fails

    def __call__(self, args: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.asked.append((args, options))
        if self.fails:
            return subprocess.CompletedProcess(args, 1, "", self.fails)
        if args[1:3] == ["database", "dump"]:
            out = Path(next(a for a in args if a.startswith("--to-path="))[10:])
            (out / "neo4j.dump").write_bytes(b"DUMP")
        return subprocess.CompletedProcess(args, 0, "done", "")


def test_a_dump_is_imported_indexed_and_dumped_away_from_the_app_s_graph(
    tmp_path: Path,
) -> None:
    write_import_bundle(rows_of(RECORDS), tmp_path / "bundle")
    admin, indexed = _Admin(), []
    home = Neo4jHome(tmp_path / "home")
    LocalNeo4jAdmin(home, run=admin, index=lambda h, data, schema: indexed.append(
        (data, schema.name))).dump(tmp_path / "bundle", tmp_path / "out.dump")

    assert (tmp_path / "out.dump").read_bytes() == b"DUMP"
    (imported, options), (dumped, _) = admin.asked
    assert imported[1:5] == ["database", "import", "full", "neo4j"]
    assert any(a.startswith("--nodes=") and a.endswith(".csv") for a in imported)
    assert any(a.startswith("--relationships=") for a in imported)
    assert dumped[1:4] == ["database", "dump", "neo4j"]
    data, schema = indexed[0]
    assert schema == "schema.cypher"
    # A scratch graph of its own: never the app's `neo4j-data`.
    assert home.data not in data.parents and not data.exists()
    assert options["env"]["JAVA_HOME"] == str(home.java_home())


def test_an_upload_carries_the_password_only_where_neo4j_admin_reads_it(tmp_path: Path) -> None:
    dump = tmp_path / "neo4j.dump"
    dump.write_bytes(b"DUMP")
    admin = _Admin()
    LocalNeo4jAdmin(Neo4jHome(tmp_path), run=admin).upload(
        dump, uri="neo4j+s://abc123.databases.neo4j.io", user="neo4j", password="s3cret")
    (args, options), = admin.asked
    assert args[1:4] == ["database", "upload", "neo4j"]
    assert "--to-uri=neo4j+s://abc123.databases.neo4j.io" in args
    assert not any("s3cret" in a for a in args)
    assert options["env"]["NEO4J_PASSWORD"] == "s3cret"


def test_what_neo4j_admin_says_on_failure_never_carries_the_password(tmp_path: Path) -> None:
    dump = tmp_path / "neo4j.dump"
    dump.write_bytes(b"DUMP")
    with pytest.raises(RuntimeError) as failed:
        LocalNeo4jAdmin(Neo4jHome(tmp_path), run=_Admin(fails="bad login s3cret")).upload(
            dump, uri="neo4j+s://abc123.databases.neo4j.io", user="neo4j", password="s3cret")
    assert "s3cret" not in str(failed.value) and "***" in str(failed.value)


def test_only_an_aura_address_is_uploaded_to(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Aura"):
        LocalNeo4jAdmin(Neo4jHome(tmp_path), run=_Admin()).upload(
            tmp_path / "x.dump", uri="bolt://example.com", user="neo4j", password="p")

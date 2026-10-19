"""`neo4j-admin`, from the app's own Neo4j (`local_neo4j.py`), on a graph of its own.

A `.dump` is what Aura's "Import Database" and Neo4j Desktop take, and what
`neo4j-admin database upload` pushes to an Aura instance. Only `neo4j-admin` writes one, so
the import bundle (`neo4j_import.py`) is loaded into a fresh database in a scratch folder,
a Neo4j is started over it only to make its indexes -- a dump carries them, the import does
not make them -- and the database is dumped. The app's own graph is never touched, and the
scratch folder goes whether that worked or not.

An upload sends the whole graph, passages and vectors to Neo4j's cloud. The password reaches
`neo4j-admin` as the `NEO4J_PASSWORD` variable it reads, never on a command line, and never
into a message.
"""
from __future__ import annotations

import re
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from graphme import processes
from graphme.adapters.export.neo4j_import import ARRAY_DELIMITER
from graphme.adapters.storage import local_neo4j
from graphme.adapters.storage.local_neo4j import Neo4jHome

AURA_URI = re.compile(r"^neo4j\+s://[A-Za-z0-9-]+\.databases\.neo4j\.io(:\d{1,5})?$")
"""What `neo4j-admin database upload` takes; anything else it turns away as an invalid URI."""
DATABASE = "neo4j"
WAIT_SECONDS = 1800
SMALL = ("512m", "256m")
"""The scratch Neo4j only makes indexes: a small heap does."""

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _indexed(port: int, schema: Path) -> None:
    """`schema.cypher` run on the scratch Neo4j, and its indexes waited for."""
    from neo4j import GraphDatabase

    statements = [line.strip().rstrip(";") for line in schema.read_text(encoding="utf-8")
                  .splitlines() if line.strip() and not line.startswith("//")]
    with GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=None) as driver:
        for statement in [*statements, "CALL db.awaitIndexes(600)"]:
            driver.execute_query(statement, database_=DATABASE)


class LocalNeo4jAdmin:
    def __init__(
        self, home: Neo4jHome, *, run: Runner = processes.run,
        index: Callable[[Neo4jHome, Path, Path], None] | None = None,
    ) -> None:
        self._home = home
        self._run = run
        self._index = index or self._index_with_neo4j

    def dump(self, bundle: Path, target: Path) -> None:
        """The bundle in `bundle` as one `.dump` file at `target`."""
        with tempfile.TemporaryDirectory(prefix="graphme-dump-") as folder:
            data = Path(folder)
            files = sorted(bundle.glob("*.csv"))
            self._admin(data, "database", "import", "full", DATABASE, "--overwrite-destination",
                        f"--array-delimiter={ARRAY_DELIMITER}", "--multiline-fields=true",
                        f"--report-file={data / 'import.report'}",
                        *(f"--nodes={f}" for f in files if f.name.startswith("nodes_")),
                        *(f"--relationships={f}" for f in files
                          if f.name.startswith("relationships_")))
            self._index(self._home, data, bundle / "schema.cypher")
            (data / "out").mkdir()
            self._admin(data, "database", "dump", DATABASE, f"--to-path={data / 'out'}")
            shutil.move(data / "out" / f"{DATABASE}.dump", target)

    def upload(self, dump: Path, *, uri: str, user: str, password: str) -> str:
        """`dump` pushed to the Aura instance at `uri`, replacing what it held; what
        `neo4j-admin` said."""
        if not AURA_URI.match(uri):
            raise ValueError("an Aura address looks like neo4j+s://<id>.databases.neo4j.io")
        with tempfile.TemporaryDirectory(prefix="graphme-upload-") as folder:
            data = Path(folder)
            (data / "from").mkdir()
            shutil.copyfile(dump, data / "from" / f"{DATABASE}.dump")
            return self._admin(
                data, "database", "upload", DATABASE, f"--from-path={data / 'from'}",
                f"--to-uri={uri}", "--overwrite-destination=true",
                env={"NEO4J_USERNAME": user, "NEO4J_PASSWORD": password}, secret=password)

    def _index_with_neo4j(self, home: Neo4jHome, data: Path, schema: Path) -> None:
        port = _free_port()
        child = local_neo4j.start(home, data, port, SMALL)
        try:
            for _ in range(local_neo4j.WAIT_SECONDS):
                if local_neo4j._answers(port):
                    break
                if child.poll() is not None:
                    raise RuntimeError("the scratch Neo4j did not start")
                time.sleep(1)
            _indexed(port, schema)
        finally:
            local_neo4j.stop(child)

    def _admin(
        self, data: Path, *args: str, env: dict[str, str] | None = None,
        secret: str | None = None,
    ) -> str:
        (data / "conf").mkdir(parents=True, exist_ok=True)
        (data / "conf" / "neo4j.conf").write_text(
            local_neo4j.conf(data, _free_port(), SMALL), encoding="utf-8")
        options: dict[str, Any] = {"capture_output": True, "text": True, "timeout": WAIT_SECONDS,
                                   "env": {**self._home.environment(data / "conf"), **(env or {})}}
        done = self._run([*self._home.command("neo4j-admin"), *args], **options)
        said = f"{done.stdout or ''}{done.stderr or ''}"
        if secret:
            said = said.replace(secret, "***")
        if done.returncode != 0:
            raise RuntimeError(f"neo4j-admin {' '.join(args[:2])} failed: {said.strip()[-800:]}")
        return said

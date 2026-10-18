"""`neo4j-admin`, run in a throwaway container of the Neo4j image.

A `.dump` is what Aura's "Import Database" and Neo4j Desktop take, and what
`neo4j-admin database upload` pushes to an Aura instance. Only `neo4j-admin` writes one, so
the import bundle (`neo4j_import.py`) is loaded into a fresh database inside a container,
its indexes are made there -- a dump carries them, the import does not make them -- and the
database is dumped. Nothing runs on this machine's own Neo4j, and the container goes
whether that worked or not.

An upload sends the whole graph, passages and vectors to Neo4j's cloud. The password goes
to the container as the `NEO4J_PASSWORD` variable `neo4j-admin` reads, never on a command
line, and never into a message.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from graphme import processes

AURA_URI = re.compile(r"^neo4j\+s://[A-Za-z0-9-]+\.databases\.neo4j\.io(:\d{1,5})?$")
"""What `neo4j-admin database upload` takes; anything else it turns away as an invalid URI."""
DATABASE = "neo4j"
WAIT_SECONDS = 1800

_DUMP = """set -e
conf=/var/lib/neo4j/conf/neo4j.conf
printf '%s\\n' dbms.security.auth_enabled=false dbms.usage_report.enabled=false \\
  server.memory.heap.max_size=512m server.memory.pagecache.size=256m >> "$conf"
TMPDIR=/tmp sh /bundle/import.sh neo4j
neo4j start
tries=0
until cypher-shell "RETURN 1" >/dev/null 2>&1; do
  tries=$((tries + 1)); [ "$tries" -lt 120 ] || { echo "neo4j did not start"; exit 1; }
  sleep 1
done
cypher-shell -f /bundle/schema.cypher
cypher-shell "CALL db.awaitIndexes(600)"
neo4j stop
mkdir -p /tmp/out
neo4j-admin database dump neo4j --to-path=/tmp/out
"""
"""Import, index, dump: the indexes are made on a running server, so it is started for
that alone, on a small heap, with no password and nothing reported to Neo4j."""

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class DockerNeo4jAdmin:
    def __init__(self, image: str, *, run: Runner = processes.run) -> None:
        self._image = image
        self._run = run

    def dump(self, bundle: Path, target: Path) -> None:
        """The bundle in `bundle` as one `.dump` file at `target`."""
        name = f"graphme-dump-{uuid.uuid4().hex[:8]}"
        self._docker("create", "--name", name, "--entrypoint", "sh", self._image, "-c", _DUMP)
        try:
            self._docker("cp", f"{bundle}/.", f"{name}:/bundle")
            self._docker("start", "-a", name)
            self._docker("cp", f"{name}:/tmp/out/{DATABASE}.dump", str(target))
        finally:
            self._docker("rm", "-f", name, check=False)

    def upload(self, dump: Path, *, uri: str, user: str, password: str) -> str:
        """`dump` pushed to the Aura instance at `uri`, replacing what it held; what
        `neo4j-admin` said."""
        if not AURA_URI.match(uri):
            raise ValueError("an Aura address looks like neo4j+s://<id>.databases.neo4j.io")
        name = f"graphme-upload-{uuid.uuid4().hex[:8]}"
        self._docker(
            "create", "--name", name,
            "-e", "NEO4J_USERNAME", "-e", "NEO4J_PASSWORD",
            "--entrypoint", "neo4j-admin", self._image,
            "database", "upload", DATABASE, "--from-path=/dump", f"--to-uri={uri}",
            "--overwrite-destination=true",
            env={**os.environ, "NEO4J_USERNAME": user, "NEO4J_PASSWORD": password},
            secret=password,
        )
        try:
            with tempfile.TemporaryDirectory() as folder:
                shutil.copyfile(dump, Path(folder) / f"{DATABASE}.dump")
                self._docker("cp", f"{folder}/.", f"{name}:/dump")
            return self._docker("start", "-a", name, secret=password)
        finally:
            self._docker("rm", "-f", name, check=False)

    def _docker(
        self, *args: str, env: dict[str, str] | None = None, check: bool = True,
        secret: str | None = None,
    ) -> str:
        options: dict[str, Any] = {"capture_output": True, "text": True, "timeout": WAIT_SECONDS}
        if env is not None:
            options["env"] = env
        done = self._run(["docker", *args], **options)
        said = f"{done.stdout or ''}{done.stderr or ''}"
        if secret:
            said = said.replace(secret, "***")
        if check and done.returncode != 0:
            raise RuntimeError(f"docker {args[0]} failed: {said.strip()[-800:]}")
        return said

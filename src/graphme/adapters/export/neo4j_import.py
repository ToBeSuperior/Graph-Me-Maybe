"""The graph space as Neo4j's own bulk import reads it (`neo4j-admin database import`).

A Cypher script carries every value as a literal the server parses, and a passage's vector
is thousands of them: a whole space in a script ran a 1 GB server out of memory before it
was half loaded. The bulk import reads typed CSV straight into a new database instead.

What is written, all in one folder:

- one node file per set of labels, so every row of a file has the same columns;
- one relationship file per type, `COVERS` from each passage to the units it was cut from;
- `schema.cypher`: the constraints and the vector and fulltext indexes, to run once the
  imported database is started -- the import makes no indexes;
- `import.sh`: the import command with every file named.

A column's type is what every value in it has in common (`ColumnKind`); where they share
none, the column is text, and a value that is not text is written as JSON.

The space is read as a stream, twice (`CorpusRows`): once to learn every column and its type,
once to write the rows under that header. A space of thousands of documents holds more
vectors than a machine holds memory; it is never held whole, and nothing is staged on disk.
"""
from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graphme.adapters.export.neo4j import SCHEMA

ARRAY_DELIMITER = ";"

NodeRow = tuple[str, str, dict[str, Any]]
"""(labels joined by ARRAY_DELIMITER, id, properties)."""
RelationshipRow = tuple[str, str, str, dict[str, Any]]
"""(type, start id, end id, properties)."""


@dataclass(frozen=True)
class CorpusRows:
    """The space as rows, each kind read afresh every time it is asked for -- the bundle is
    written in two passes, and neither holds the space."""

    nodes: Callable[[], Iterable[NodeRow]]
    relationships: Callable[[], Iterable[RelationshipRow]]
    indexes: Sequence[dict[str, Any]]
    documents: int
    """How many documents leave."""


class ColumnKind:
    """The import type every value of a column shares, `string` when they share none -- read
    one value at a time, as the rows stream past."""

    def __init__(self) -> None:
        self.present = False
        self.lists = self.bools = self.ints = self.numbers = True
        self.items: ColumnKind | None = None
        self.text_only, self.delimited = True, False

    def add(self, value: Any) -> None:
        if value is None:
            return
        self.present = True
        if isinstance(value, list):
            self.bools = self.ints = self.numbers = False
            self.items = self.items or ColumnKind()
            if value and set(map(type, value)) == {float}:
                # A vector: thousands of floats, taken in one look rather than one by one.
                self.items.present = True
                self.items.lists = self.items.bools = self.items.ints = False
                self.text_only = False
                return
            for item in value:
                self.items.add(item)
                if not isinstance(item, str):
                    self.text_only = False
                elif ARRAY_DELIMITER in item:
                    self.delimited = True
            return
        self.lists = False
        if isinstance(value, bool):
            self.ints = self.numbers = False
            return
        self.bools = False
        if not isinstance(value, int):
            self.ints = False
            if not isinstance(value, float):
                self.numbers = False

    def kind(self) -> str:
        if not self.present:
            return "string"
        if self.lists:
            inner = self.items.kind() if self.items is not None else "string"
            if inner == "string" and not self.text_only:
                return "string"
            if self.text_only and self.delimited:
                return "string"
            return f"{inner}[]"
        if self.bools:
            return "boolean"
        if self.ints:
            return "long"
        if self.numbers:
            return "double"
        return "string"


def _cell(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == "double[]":
        return ARRAY_DELIMITER.join(map(repr, value))  # a vector: no call per number
    if kind.endswith("[]"):
        return ARRAY_DELIMITER.join(_cell(item, kind[:-2]) for item in value)
    if kind == "boolean":
        return "true" if value else "false"
    if kind in ("long", "double"):
        return repr(value)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def write_import_bundle(rows: CorpusRows, folder: Path) -> list[Path]:
    """Every file the import reads, `schema.cypher` and `import.sh`, written into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)

    node_columns: dict[str, dict[str, ColumnKind]] = {}
    for labels, _, properties in rows.nodes():
        columns = node_columns.setdefault(labels, {})
        for name, value in properties.items():
            if name != "id":
                columns.setdefault(name, ColumnKind()).add(value)
    relationship_columns: dict[str, dict[str, ColumnKind]] = {}
    for kind, _, _, properties in rows.relationships():
        columns = relationship_columns.setdefault(kind, {})
        for name, value in properties.items():
            columns.setdefault(name, ColumnKind()).add(value)

    node_files = {
        labels: folder / f"nodes_{labels.replace(ARRAY_DELIMITER, '_')}.csv"
        for labels in sorted(node_columns)
    }
    relationship_files = {
        kind: folder / f"relationships_{kind}.csv" for kind in sorted(relationship_columns)
    }
    with ExitStack() as files:
        out = _writers(files, node_files)
        typed = {labels: _typed(columns) for labels, columns in node_columns.items()}
        for labels, names in typed.items():
            out[labels].writerow(["id:ID", ":LABEL", *(f"{n}:{k}" for n, k in names)])
        for labels, node_id, properties in rows.nodes():
            _unchanged(labels, properties, typed, skip={"id"})
            out[labels].writerow([node_id, labels, *(
                _cell(properties.get(name), kind) for name, kind in typed[labels])])
    with ExitStack() as files:
        out = _writers(files, relationship_files)
        typed = {kind: _typed(columns) for kind, columns in relationship_columns.items()}
        for kind, names in typed.items():
            out[kind].writerow([":START_ID", ":END_ID", ":TYPE", *(f"{n}:{k}" for n, k in names)])
        for kind, start, end, properties in rows.relationships():
            _unchanged(kind, properties, typed, skip=set())
            out[kind].writerow([start, end, kind, *(
                _cell(properties.get(name), column) for name, column in typed[kind])])

    schema = folder / "schema.cypher"
    # LF on every system: these are read by POSIX tools, where Windows' CRLF made `set -e`
    # into `set -e\r` and every .dump made on Windows failed.
    schema.write_text("\n".join(_schema(rows.indexes)) + "\n", encoding="utf-8", newline="\n")
    command = folder / "import.sh"
    command.write_text(
        _command(list(node_files.values()), list(relationship_files.values())),
        encoding="utf-8", newline="\n")
    command.chmod(0o755)
    guide = folder / "README.md"
    guide.write_text(_GUIDE, encoding="utf-8", newline="\n")
    return [*node_files.values(), *relationship_files.values(), schema, command, guide]


_GUIDE = """# Graph space for Neo4j

This folder is a graph space as Neo4j's bulk import reads it: documents, their outlines,
the links between documents, and the passages with their vectors.

- `nodes_*.csv`, `relationships_*.csv` -- typed CSV, one file per label set and relationship
  type. `Chunk` nodes are the passages; `COVERS` joins each to the units it was cut from.
- `import.sh` -- loads the folder into a new, stopped database, replacing what it held:
  `./import.sh [database]` (default `neo4j`), with `neo4j-admin` on the PATH.
- `schema.cypher` -- the constraints and the vector and fulltext indexes. The import makes
  no indexes: start the database, then run it once,
  `cypher-shell -u neo4j -p <password> -f schema.cypher`.

For Neo4j Aura, turn the folder into a `.dump` (`graphme export-corpus <file> --dump`) and
load that with Aura's Import Database. Once the indexes are online, neo4j-graphrag's
`HybridCypherRetriever` searches the `chunk_embedding` vector index and the `chunk_text`
fulltext index.
"""


def _writers(files: ExitStack, paths: dict[str, Path]) -> dict[str, Any]:
    """One CSV writer per file, every file closed when `files` closes -- those opened before
    one that fails too."""
    return {key: csv.writer(files.enter_context(path.open("w", encoding="utf-8", newline="")))
            for key, path in paths.items()}


def _unchanged(key: str, properties: dict[str, Any], typed: dict[str, list[tuple[str, str]]],
               *, skip: set[str]) -> None:
    """The second pass meets only what the first pass saw: a space changed between them would
    write rows under a header that does not name them."""
    names = {name for name, _ in typed.get(key, [])}
    if key not in typed or not set(properties) - skip <= names:
        raise RuntimeError("the graph space changed while it was exported; export it again")


def _typed(columns: dict[str, ColumnKind]) -> list[tuple[str, str]]:
    return [(name, columns[name].kind()) for name in sorted(columns)]


def _schema(indexes: Sequence[dict[str, Any]]) -> list[str]:
    lines = [f"{statement};" for statement in SCHEMA]
    for index in indexes:
        target = f"FOR (c:{index['label']})"
        if index["kind"] == "VECTOR":
            lines.append(
                f"CREATE VECTOR INDEX {index['name']} IF NOT EXISTS {target} "
                f"ON (c.{index['property']}) OPTIONS {{indexConfig: "
                f"{{`vector.dimensions`: {index['dimensions']}, "
                f"`vector.similarity_function`: '{index['similarity']}'}}}};"
            )
        else:
            lines.append(
                f"CREATE FULLTEXT INDEX {index['name']} IF NOT EXISTS {target} "
                f"ON EACH [c.{index['property']}] OPTIONS {{indexConfig: "
                f"{{`fulltext.analyzer`: '{index.get('analyzer', 'cjk')}'}}}};"
            )
    return lines


def import_arguments(
    node_files: Iterable[Path | str], relationship_files: Iterable[Path | str], report: str
) -> list[str]:
    """What `neo4j-admin database import full <database>` is given for this bundle -- by
    `import.sh` and by the app's own `.dump` alike, so the two cannot drift."""
    return [
        "--overwrite-destination",
        f"--array-delimiter={ARRAY_DELIMITER}",
        "--multiline-fields=true",
        f"--report-file={report}",
        *(f"--nodes={path}" for path in node_files),
        *(f"--relationships={path}" for path in relationship_files),
    ]


def _command(node_files: Sequence[Path], relationship_files: Sequence[Path]) -> str:
    # The bundle may be on a read-only folder; the report is not part of it.
    arguments = import_arguments([path.name for path in node_files],
                                 [path.name for path in relationship_files],
                                 "${TMPDIR:-/tmp}/graphme-import.report")
    return "\n".join(
        [
            "#!/bin/sh",
            "# Loads this folder into a new, stopped Neo4j database, replacing what it held.",
            "# Then start the database and run schema.cypher once:",
            "#   cypher-shell -u neo4j -p <password> -f schema.cypher",
            "# Usage: ./import.sh [database]   (default: neo4j)",
            "set -e",
            'cd "$(dirname "$0")"',
            'neo4j-admin database import full "${1:-neo4j}" \\',
            *(f'  "{argument}" \\' for argument in arguments),
            "  --verbose",
            "",
        ]
    )

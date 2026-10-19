"""`python -m graphme.neo4j_install HOME`: the app's own Neo4j and Java fetched into HOME --
what the installers run. (Its own module: run as `-m`, the package must not have imported it.)"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    # The installers' console may be cp1252 (English Windows): never fail over a character.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m graphme.neo4j_install HOME", file=sys.stderr)
        return 2
    from graphme.adapters.storage.local_neo4j import install

    try:
        install(Path(args[0]), say=lambda line: print(line, flush=True))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"the graph DB (Neo4j) did not download: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

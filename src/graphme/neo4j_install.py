"""`python -m graphme.neo4j_install HOME`: the app's own Neo4j and Java fetched into HOME --
what the installers run. (Its own module: run as `-m`, the package must not have imported it.)"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m graphme.neo4j_install HOME", file=sys.stderr)
        return 2
    from graphme.adapters.storage.local_neo4j import install

    try:
        install(Path(args[0]), say=lambda line: print(line, flush=True))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"그래프 DB(Neo4j)를 받지 못했습니다: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

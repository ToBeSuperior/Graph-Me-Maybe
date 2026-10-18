"""Local demo from a checkout: `graphme app`'s server, run from the repository with its `.env`
and `frontend/dist`.

    python tools/hierarchy_demo/app.py [port]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from graphme.server.demo import run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    os.chdir(REPO)  # settings read `.env` from the working directory, as the CLI does
    print(f"\n  Graph-Me-Maybe · http://127.0.0.1:{port}\n")
    run(port, None)  # the checkout's frontend/dist, as the server finds it


if __name__ == "__main__":
    main()

"""Files written whole: a crash or a second writer never leaves one half written."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def write_atomic(path: Path, data: str | bytes, *, suffix: str = ".tmp",
                 sync: bool = True) -> None:
    """`data` written beside `path` under a name of its own, then put in its place. `sync`
    puts it on disk first (settings and records); a cache that can be made again skips it. A
    file already there keeps its permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data.encode("utf-8") if isinstance(data, str) else data)
            if sync:
                out.flush()
                os.fsync(out.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

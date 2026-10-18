from __future__ import annotations

import os
import tempfile
from pathlib import Path

from graphme.domain.ids import sha256_bytes


class LocalAssetStore:
    """Content-addressed asset store with atomic writes under a local root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def path_for(self, content_hash: str, suffix: str = "") -> Path:
        return self._root / content_hash[:2] / f"{content_hash}{suffix}"

    def put(self, content: bytes, suffix: str = "") -> str:
        digest = sha256_bytes(content)
        target = self.path_for(digest, suffix)
        if target.exists():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return digest

    def read(self, content_hash: str, suffix: str = "") -> bytes:
        """The asset with this hash, whatever spelling it was written under.

        The hash is what names an asset; the suffix is only so a file is openable. One
        media type is written with more than one of them -- a JPEG arrives as `.jpg` from
        one format and `.jpeg` from another -- and a reader that guesses the other spelling
        would not find a file that is plainly there.
        """
        exact = self.path_for(content_hash, suffix)
        if exact.exists():
            return exact.read_bytes()
        for other in sorted(exact.parent.glob(f"{content_hash}.*")):
            return other.read_bytes()
        return exact.read_bytes()  # the error names the path that was asked for

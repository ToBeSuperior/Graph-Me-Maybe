from pathlib import Path
from typing import Protocol


class AssetStore(Protocol):
    def put(self, content: bytes, suffix: str = "") -> str: ...

    def path_for(self, content_hash: str, suffix: str = "") -> Path: ...

    def read(self, content_hash: str, suffix: str = "") -> bytes: ...

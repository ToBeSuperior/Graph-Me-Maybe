import hashlib
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ID_NAMESPACE = uuid.UUID("6e23f4cc-9b29-4ab0-a575-0ed92f9f2cbc")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_locator(kind: str, coordinates: Mapping[str, Any]) -> str:
    return f"{kind}:{canonical_json(dict(coordinates))}"


def stable_id(document_hash: str, locator: str) -> str:
    if len(document_hash) != 64 or any(char not in "0123456789abcdef" for char in document_hash):
        raise ValueError("document_hash must be a lowercase SHA-256 hex digest")
    if not locator:
        raise ValueError("locator must not be empty")
    return str(uuid.uuid5(ID_NAMESPACE, f"{document_hash}:{locator}"))


def content_id(kind: str, content_hash: str) -> str:
    if not kind or not content_hash:
        raise ValueError("kind and content_hash must not be empty")
    return str(uuid.uuid5(ID_NAMESPACE, f"content:{kind}:{content_hash}"))

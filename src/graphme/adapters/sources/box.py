"""Box as a place originals are read from (`ports.sources.SourceStore`).

A Box document is recorded as `box://<file id>/<name>`. What the user picks -- folders and files,
from a listing a level at a time -- is opened into the documents in it when a build starts. A
file's bytes are fetched only while they are needed, into a folder of their own under the app's,
and removed after. Nothing is written to Box.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from graphme.application.embedded_documents import safe_name
from graphme.application.ingest import SUFFIX_FORMATS

API = "https://api.box.com/2.0"
SCHEME = "box"
ROOT = "0"
"""Box's id for the top of a user's files."""
FIELDS = "id,type,name,size,sha1,file_version,trashed_at"
PAGE = 1000


def origin(file_id: str, name: str) -> str:
    return f"{SCHEME}://{file_id}/{name}"


def file_id_of(origin_: str) -> str:
    return origin_.removeprefix(f"{SCHEME}://").split("/", 1)[0]


def web_url(origin_: str) -> str:
    """Where a reader opens a Box file in the browser."""
    return f"https://app.box.com/file/{file_id_of(origin_)}"


@dataclass(frozen=True)
class Picked:
    """A folder or a file the user picked in a listing."""

    type: str  # folder | file
    id: str
    name: str


@dataclass(frozen=True)
class BoxFile:
    """One document as Box describes it."""

    id: str
    name: str
    version: str | None
    """Box's id for this content: a file edited since has another."""
    sha1: str | None

    @property
    def origin(self) -> str:
        return origin(self.id, self.name)


def _is_document(item: dict[str, Any]) -> bool:
    return item.get("type") == "file" and not item.get("trashed_at") and \
        PurePosixPath(str(item.get("name", ""))).suffix.lower() in SUFFIX_FORMATS


class BoxSource:
    """Reads what the signed-in account may read, with a token from `access_token`."""

    def __init__(self, http: httpx.Client, access_token: Callable[[], str], scratch: Path) -> None:
        self._http = http
        self._token = access_token
        self._scratch = scratch

    def _get(self, path: str, **params: Any) -> httpx.Response:
        response = self._http.get(f"{API}/{path}", params=params, timeout=120,
                                  follow_redirects=True,
                                  headers={"Authorization": f"Bearer {self._token()}"})
        if response.status_code in (403, 404):  # gone, or no longer this account's to read
            raise FileNotFoundError(path)
        response.raise_for_status()
        return response

    def _items(self, folder_id: str) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            said = self._get(f"folders/{folder_id}/items", fields=FIELDS, limit=PAGE,
                             offset=offset).json()
            entries = said.get("entries", [])
            yield from entries
            offset += len(entries)
            if not entries or offset >= int(said.get("total_count", 0)):
                return

    def folder(self, folder_id: str = ROOT) -> dict[str, Any]:
        """A folder as the page lists it: where it is, its folders, then its documents."""
        said = self._get(f"folders/{folder_id}", fields="id,name,path_collection").json()
        items = [item for item in self._items(folder_id)
                 if item.get("type") == "folder" or _is_document(item)]
        items.sort(key=lambda i: (i.get("type") != "folder", str(i.get("name", "")).lower()))
        return {
            "id": str(said.get("id", folder_id)), "name": str(said.get("name", "")),
            "path": [{"id": str(p["id"]), "name": str(p["name"])}
                     for p in (said.get("path_collection") or {}).get("entries", [])],
            "items": [{"id": str(i["id"]), "type": str(i["type"]), "name": str(i["name"]),
                       **({"size": int(i["size"])} if i.get("size") is not None else {})}
                      for i in items],
        }

    def documents(self, picked: Iterable[Picked]) -> tuple[list[BoxFile], list[str]]:
        """The documents in what was picked -- each folder's, all the way down -- once each,
        and the names of what could no longer be read."""
        found: dict[str, BoxFile] = {}
        lost: list[str] = []
        queue = list(picked)
        seen: set[tuple[str, str]] = set()
        while queue:
            item = queue.pop(0)
            if (item.type, item.id) in seen:
                continue
            seen.add((item.type, item.id))
            try:
                if item.type == "folder":
                    for child in self._items(item.id):
                        if child.get("type") == "folder":
                            queue.append(Picked("folder", str(child["id"]), str(child["name"])))
                        elif _is_document(child):
                            found.setdefault(str(child["id"]), _file(child))
                    continue
                said = self._get(f"files/{item.id}", fields=FIELDS).json()
            except FileNotFoundError:
                lost.append(item.name)
                continue
            if _is_document(said):
                found.setdefault(item.id, _file(said))
        return list(found.values()), lost

    @contextmanager
    def open_copy(self, origin_: str) -> Iterator[Path]:
        """The file's bytes in a folder of their own, removed when the context ends."""
        name = safe_name(origin_.rsplit("/", 1)[-1])
        self._scratch.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="box-", dir=self._scratch))
        try:
            copy = folder / name
            copy.write_bytes(self._get(f"files/{file_id_of(origin_)}/content").content)
            yield copy
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def web_url(self, origin_: str) -> str | None:
        return web_url(origin_)


def _file(item: dict[str, Any]) -> BoxFile:
    version = (item.get("file_version") or {}).get("id")
    return BoxFile(id=str(item["id"]), name=str(item["name"]),
                   version=str(version) if version else None, sha1=item.get("sha1"))

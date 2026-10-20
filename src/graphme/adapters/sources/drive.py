"""Google Drive as a place originals are read from (`ports.sources.SourceStore`).

A Drive document is recorded as `gdrive://<file id>/<name>`. Its bytes are fetched only while
they are needed -- to read it, to draw a page a reader looks at, to put it in a download -- into
a folder of their own under the app's, and removed after. Nothing is written to Drive.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from graphme.application.embedded_documents import safe_name
from graphme.application.ingest import SUFFIX_FORMATS

API = "https://www.googleapis.com/drive/v3"
SCHEME = "gdrive"
FOLDER = "application/vnd.google-apps.folder"
FIELDS = "id,name,mimeType,sha256Checksum,headRevisionId,webViewLink,trashed"


def origin(file_id: str, name: str) -> str:
    return f"{SCHEME}://{file_id}/{name}"


def file_id_of(origin_: str) -> str:
    return origin_.removeprefix(f"{SCHEME}://").split("/", 1)[0]


def web_url(origin_: str) -> str:
    """Where a reader opens a Drive file in the browser."""
    return f"https://drive.google.com/file/d/{file_id_of(origin_)}/view"


@dataclass(frozen=True)
class DriveFile:
    """One file as Drive describes it."""

    id: str
    name: str
    sha256: str | None
    revision: str | None
    web_url: str | None

    @property
    def origin(self) -> str:
        return origin(self.id, self.name)


class DriveSource:
    """Reads what the signed-in account may read, with a token from `access_token`."""

    def __init__(self, http: httpx.Client, access_token: Callable[[], str], scratch: Path) -> None:
        self._http = http
        self._token = access_token
        self._scratch = scratch

    def _get(self, path: str, **params: Any) -> httpx.Response:
        response = self._http.get(f"{API}/{path}", params=params, timeout=120,
                                  headers={"Authorization": f"Bearer {self._token()}"})
        if response.status_code in (403, 404):  # gone, or no longer this account's to read
            raise FileNotFoundError(path)
        response.raise_for_status()
        return response

    def describe(self, file_id: str) -> tuple[DriveFile | None, str]:
        """The file (None for a folder, a trashed file or one this app cannot read as a
        document), and its MIME type."""
        said = self._get(f"files/{file_id}", fields=FIELDS, supportsAllDrives="true").json()
        return _document(said), str(said.get("mimeType", ""))

    def documents(self, picked: list[str]) -> tuple[list[DriveFile], list[str]]:
        """The documents among what was picked -- a folder's, as far as Drive lets this app see
        into it -- and the ids that could not be read."""
        found: dict[str, DriveFile] = {}
        lost: list[str] = []
        queue = list(picked)
        seen: set[str] = set()
        while queue:
            file_id = queue.pop(0)
            if file_id in seen:
                continue
            seen.add(file_id)
            try:
                document, kind = self.describe(file_id)
            except FileNotFoundError:
                lost.append(file_id)
                continue
            if document is not None:
                found[document.id] = document
            elif kind == FOLDER:
                queue += self._children(file_id)
        return list(found.values()), lost

    def _children(self, folder_id: str) -> list[str]:
        ids: list[str] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id)", "pageSize": 1000,
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            try:
                said = self._get("files", **params).json()
            except FileNotFoundError:
                return ids
            ids += [str(f["id"]) for f in said.get("files", [])]
            token = said.get("nextPageToken")
            if not token:
                return ids

    @contextmanager
    def open_copy(self, origin_: str) -> Iterator[Path]:
        """The file's bytes in a folder of their own, removed when the context ends."""
        name = safe_name(origin_.rsplit("/", 1)[-1])
        self._scratch.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="drive-", dir=self._scratch))
        try:
            copy = folder / name
            with copy.open("wb") as written:
                written.write(self._get(f"files/{file_id_of(origin_)}", alt="media",
                                        supportsAllDrives="true").content)
            yield copy
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def web_url(self, origin_: str) -> str | None:
        return web_url(origin_)


def _document(said: dict[str, Any]) -> DriveFile | None:
    """A file the pipeline reads; a folder, a Google web format (Docs, Sheets, Slides) or a
    trashed file is none."""
    name = str(said.get("name", ""))
    if said.get("trashed") or PurePosixPath(name).suffix.lower() not in SUFFIX_FORMATS:
        return None
    if str(said.get("mimeType", "")).startswith("application/vnd.google-apps."):
        return None
    return DriveFile(id=str(said["id"]), name=name, sha256=said.get("sha256Checksum"),
                     revision=said.get("headRevisionId"), web_url=said.get("webViewLink"))

"""Documents from a cloud drive: Box through its API, and a drive's desktop client on
this machine."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from graphme.adapters.sources.box import Picked as BoxPicked
from graphme.adapters.sources.cloud_files import local_usage
from graphme.adapters.sources.desktop_drives import DESKTOP_DRIVES, Drive, listing
from graphme.server import state
from graphme.server.corpus_builds import CorpusBuilds
from graphme.server.jobs import (
    Busy,
    CorpusBuild,
)


class CloudSources(CorpusBuilds):
    """Box and the cloud drives' desktop clients."""

    def _box_kept(self) -> dict[str, Any]:
        try:
            said = json.loads((self.root / "box.json").read_text(encoding="utf-8"))
            return said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            return {}

    def _box_keep(self, kept: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "box.json").write_text(json.dumps(kept), encoding="utf-8")

    @property
    def box_picked(self) -> list[BoxPicked]:
        """The Box folders and files the user picked, in the order picked."""
        return [BoxPicked(str(i["type"]), str(i["id"]), str(i["name"]))
                for i in self._box_kept().get("picked", [])
                if isinstance(i, dict) and i.get("type") in ("folder", "file")]

    def box_state(self) -> dict[str, Any]:
        configured = bool(self.settings.box_client_id and self.settings.box_client_secret)
        return {"configured": configured, "connected": configured and self.box().connected(),
                "picked": [{"type": p.type, "id": p.id, "name": p.name}
                           for p in self.box_picked]}

    def box_pick(self, items: Sequence[BoxPicked]) -> dict[str, Any]:
        """`items` are what is picked now, in place of what was."""
        kept = self._box_kept()
        kept["picked"] = [{"type": i.type, "id": i.id, "name": i.name}
                          for i in dict.fromkeys(items)]
        self._box_keep(kept)
        return self.box_state()

    def box_folder(self, folder_id: str) -> dict[str, Any]:
        """A Box folder a level at a time, each item marked when it is picked itself."""
        listed = self._box_source().folder(folder_id)
        picked = {(p.type, p.id) for p in self.box_picked}
        for item in listed["items"]:
            item["picked"] = (item["type"], item["id"]) in picked
        return listed

    def box_disconnect(self) -> None:
        self.box().disconnect()
        (self.root / "box.json").unlink(missing_ok=True)

    def build_box(self) -> CorpusBuild:
        """The documents in what was picked in Box, read into the space. A file is known by
        the version Box states: one whose version was read before and whose content the
        space still holds is passed over without being fetched; the rest is fetched for the
        build and removed after it. Listed and fetched once the build has started."""
        self.not_in_mail_workspace()
        box = self._box_source()

        def find(build: CorpusBuild) -> list[str]:
            try:
                found, lost = box.documents(self.box_picked)
            except (LookupError, httpx.HTTPError) as error:  # signed out, or Box refused
                raise ValueError(str(error)) from None
            build.failed += [f"{name}: no longer readable in Box" for name in lost]
            kept = self._box_kept()
            versions: dict[str, str] = dict(kept.get("versions", {}))
            """Box version id -> the SHA-256 of its content, as read here."""
            held = self.space().contents([versions[f.version] for f in found
                                          if f.version in versions])
            docs: list[str] = []
            for wanted in found:
                if wanted.version in versions and versions[wanted.version] in held:
                    continue
                folder = self.root / "box" / wanted.id
                shutil.rmtree(folder, ignore_errors=True)
                folder.mkdir(parents=True)
                with box.open_copy(wanted.origin) as copy:
                    copied = folder / copy.name
                    shutil.copyfile(copy, copied)
                if wanted.version and (digest := state.content_hash(copied)):
                    versions[wanted.version] = digest
                doc = self.open_path(str(copied))
                self.origins[doc] = (wanted.origin, {
                    "box_file_id": wanted.id, "box_version": wanted.version,
                    "web_url": box.web_url(wanted.origin)})
                docs.append(doc)
            kept["versions"] = versions
            self._box_keep(kept)
            build.known += len(found) - len(docs)
            return docs

        return self.build_corpus([], "Box", find=find, empty_done=True)

    @staticmethod
    def desktop_drive(kind: str) -> Drive | None:
        """The folder `kind`'s desktop client shows on this machine, when it is here."""
        return DESKTOP_DRIVES[kind](Path.home(), platform=sys.platform, environ=os.environ)

    def drive_connected(self, kind: str) -> bool:
        """Whether the user said to read `kind`: asked once, then kept for every start."""
        try:
            said = json.loads((self.root / f"{kind}-drive.json").read_text(encoding="utf-8"))
            return bool(said.get("connected"))
        except (OSError, ValueError, AttributeError):
            return False

    def drive_connect(self, kind: str, connected: bool) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{kind}-drive.json").write_text(json.dumps({"connected": connected}),
                                                      encoding="utf-8")

    def drive_state(self, kind: str) -> dict[str, Any]:
        drive = self.desktop_drive(kind)
        return {"found": drive is not None, "root": str(drive.base) if drive else None,
                "connected": drive is not None and self.drive_connected(kind)}

    def drive_folder(self, kind: str, relative: str) -> dict[str, Any]:
        """A folder of `kind`'s, a level at a time; LookupError when its client is not here,
        Busy until the user has said to read it."""
        self.not_in_mail_workspace()
        drive = self._connected_drive(kind)
        return listing(drive.base, relative, drive.top)

    def drive_usage(self, kind: str) -> dict[str, int]:
        """What `kind` keeps on this machine: its bytes and how many files. Never a listing
        asked of the cloud -- a folder never opened holds nothing here and is passed over."""
        drive = self._connected_drive(kind)
        return local_usage(drive.base, drive.top)

    def _connected_drive(self, kind: str) -> Drive:
        """`kind`'s folder on this machine; LookupError when its client is not here, Busy
        until the user has said to read it."""
        drive = self.desktop_drive(kind)
        if drive is None:
            raise LookupError(f"{kind} drive is not on this machine")
        if not self.drive_connected(kind):
            raise Busy(f"{kind} drive is not connected -- connect it first")
        return drive

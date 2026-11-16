"""The graph space leaving this machine: an import bundle, the documents' files, a dump,
an upload to Aura (`ExportJob`)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from collections.abc import Sequence
from pathlib import Path

from graphme.adapters.export.neo4j_import import write_import_bundle
from graphme.application.embedded_documents import name_part, shown_name
from graphme.server import state
from graphme.server.cloud_sources import CloudSources
from graphme.server.jobs import (
    EXPORTING,
    IN_LOG,
    Busy,
    ExportJob,
)


class Exports(CloudSources):
    """Exports of the graph space and of the files it was read from."""

    def bundle_zip(self, docs: list[str] | None) -> Path:
        """The space -- or the documents `docs` names -- as a zip of what
        `neo4j-admin database import` reads: a file of its own per request, which the caller
        removes once it is sent."""
        with tempfile.TemporaryDirectory() as folder:
            write_import_bundle(self.space().corpus_rows(docs), Path(folder))
            place = Path(tempfile.mkdtemp(prefix="neo4j-import-"))  # a dropped download: temp
            return Path(shutil.make_archive(str(place / "neo4j-import"), "zip", folder))

    def files_zip(self, picked: Sequence[tuple[str, str]]) -> Path:
        """The files the documents in `picked` were read from, each in the folder named beside
        it, as one zip of its own per request, which the caller removes once it is sent. A
        document the space does not hold, or whose file is gone, is named in
        `missing-files.txt` rather than passed over."""
        space = self.space()
        clouded: list[Path] = []
        """Online only before they were zipped: given back once they are."""
        place = Path(tempfile.mkdtemp(prefix="graphme-files-"))  # a dropped download: temp
        archive = place / "files.zip"
        taken: set[str] = set()
        missing: list[str] = []
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
            for document_id, folder in picked:
                path = space.source_path(document_id)
                # A folder name is one folder: no separator, no way out of the zip.
                where = name_part(folder, "files")
                if not path:
                    missing.append(f"{document_id}\tnot in the graph")
                    continue
                stem, suffix = os.path.splitext(shown_name(Path(path).name))
                name, n = f"{where}/{stem}{suffix}", 1
                while name in taken:  # two files of one name in one folder: both kept
                    n += 1
                    name = f"{where}/{stem} ({n}){suffix}"
                local = Path(path)
                if "://" not in path and local.name and state.online_only(local) \
                        and self.doc_id(path) not in self.viewed:  # one looked at: kept a while
                    clouded.append(local)
                try:
                    with self.sources.open_copy(path) as copy:
                        out.write(copy, name)
                except (FileNotFoundError, LookupError):
                    # Named as a reader can find it: where it was for, and where it was.
                    missing.append(f"{where}/{stem}{suffix}\tnot found at {path}")
                    continue
                taken.add(name)
            if missing:
                out.writestr("missing-files.txt", "These could not be added:\n"
                             + "\n".join(missing) + "\n")
        state._give_back_for_good(clouded)
        return archive

    def start_export(
        self, kind: str, docs: list[str] | None, aura: dict[str, str] | None = None
    ) -> ExportJob:
        """A `.dump` of the space, and for an upload that dump pushed to Aura, in the
        background. `aura` -- address, user, password -- lives only in the thread."""
        with self.exporting:
            if self.export.state in EXPORTING:
                raise Busy("an export is already running")
            job = self.export = ExportJob(
                id=uuid.uuid4().hex, kind=kind, state="dumping", message="making the dump")
            self.jobs[job.id] = job
        threading.Thread(target=self._export, args=(job, docs, aura), daemon=True).start()
        return job

    def _export(
        self, job: ExportJob, docs: list[str] | None, aura: dict[str, str] | None
    ) -> None:
        try:
            self.space()  # opened under `opening_space`, like every other first use
            job.documents = self.graph.export(self.dump_path, dump=True, documents=docs)
            if aura is not None:
                job.state, job.message = "uploading", "uploading to Aura"
                said = state.create_neo4j_admin(self.settings).upload(self.dump_path, **aura)
                job.message = said.strip().splitlines()[-1] if said.strip() else ""
            else:
                job.message = f"{self.dump_path.stat().st_size // 1024} KB"
            job.finished_at = time.time()
            job.state = "done"
        except Exception as error:  # noqa: BLE001 -- the job's state says what happened
            # The log has all of it; the page, its first line -- never the password.
            traceback.print_exc(file=sys.stderr)
            said = (str(error).strip().splitlines() or [type(error).__name__])[0][:300]
            if aura and aura.get("password"):
                said = said.replace(aura["password"], "***")
            job.state, job.message = "error", f"{said} {IN_LOG}"

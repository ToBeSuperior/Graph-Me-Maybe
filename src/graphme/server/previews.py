"""Pages drawn for a reader when looked at, and a document's original opened on this
machine."""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

from graphme.application.embedded_documents import shown_name
from graphme.domain.enums import DocumentFormat
from graphme.server import state
from graphme.server.jobs import (
    GONE,
    UNDRAWN,
    Undrawn,
)
from graphme.server.reading import Reading


class Previews(Reading):
    """Pages drawn for readers, kept a while, and what a drive downloaded for them given back."""

    def preview(self, doc: str, index: int) -> bytes:
        """Page `index` of a document drawn for a reader: no page is kept for every document,
        only the pages looked at -- the latest PREVIEWS_KEPT in memory, and on disk as many as
        `preview_cache_mb` holds, so a page looked at before needs no file, and a cloud drive
        does not download it again to show it."""
        session = self.session(doc)
        if session.format is DocumentFormat.EML:
            raise LookupError("a message has no pages")
        key = (doc, index, session.stamp)
        with self.previewing:
            if key in self.previews:
                self.previews.move_to_end(key)
                return self.previews[key]
        original = session.origin or str(session.source)
        local = None if "://" in original else Path(original)  # a cloud address: fetched
        stamp = state._stamp(local) if local is not None else session.stamp
        page = (f"{doc}-{stamp[0]}-{stamp[1]}-{index}.png" if stamp is not None
                else None)
        if page is not None and (kept := self.preview_dir / page).is_file():
            with contextlib.suppress(OSError):
                png = kept.read_bytes()
                os.utime(kept)  # looked at again: the last to go
                with self.previewing:
                    self.previews[key] = png
                return png
        # A cloud drive's file kept online only is downloaded to be drawn: given back once it
        # has not been looked at for a while (the next page is drawn from it meanwhile).
        if local is not None and (state.online_only(local) or doc in self.viewed):
            self._viewed(doc, local)
        try:
            with self.sources.open_copy(original) as source, self.drawing:
                try:
                    png = self.renderer.preview(source, session.format, index)
                except Exception as error:
                    raise Undrawn(UNDRAWN) from error
        except FileNotFoundError as error:
            raise LookupError(GONE) from error
        with self.previewing:
            self.previews[key] = png
            while len(self.previews) > state.PREVIEWS_KEPT:
                self.previews.popitem(last=False)
        if page is not None:
            with contextlib.suppress(OSError):  # not kept: drawn again next time
                state._keep_page(self.preview_dir, page, png, state.preview_limit(self.settings))
            assert stamp is not None
            self._draw_around(doc, index, original, session.format, stamp)
        return png

    def _draw_around(self, doc: str, index: int, original: str, document_format: DocumentFormat,
                     stamp: tuple[int, int]) -> None:
        """The PREVIEW_AROUND pages before and after `index` not kept yet, drawn behind the
        reader in one go and kept on disk; one document's at a time."""
        def name(at: int) -> str:
            return f"{doc}-{stamp[0]}-{stamp[1]}-{at}.png"

        around = state.PREVIEW_AROUND
        wanted = [at for at in range(max(1, index - around), index + around + 1)
                  if at != index and not (self.preview_dir / name(at)).is_file()]
        with self.previewing:
            if not wanted or doc in self.drawing_around:
                return
            self.drawing_around.add(doc)

        def draw() -> None:
            try:
                with self.sources.open_copy(original) as source, self.drawing:
                    drawn = self.renderer.previews(source, document_format, wanted)
                for at, png in drawn.items():
                    with contextlib.suppress(OSError):
                        state._keep_page(self.preview_dir, name(at), png,
                                         state.preview_limit(self.settings))
            except Exception:  # noqa: BLE001 -- a page not drawn ahead is drawn when opened
                state._log.debug("pages around %s not drawn ahead", index, exc_info=True)
            finally:
                with self.previewing:
                    self.drawing_around.discard(doc)

        threading.Thread(target=draw, daemon=True).start()

    def open_original(self, path: str) -> str:
        """The file a document was read from, opened in its own app on this machine, and the
        name it is opened under. A file kept by a desktop drive is opened where it is (the drive
        fetches it); one only a cloud drive's API holds is fetched to a copy of its own first,
        kept so the app opening it can read it. FileNotFoundError: the file is gone."""
        if "://" not in path:
            local = Path(path)
            if not local.is_file():
                raise FileNotFoundError(path)
            self.open_file(local)
            return local.name
        name = shown_name(Path(path).name)
        folder = self.root / "opened" / hashlib.sha256(path.encode()).hexdigest()[:16]
        folder.mkdir(parents=True, exist_ok=True)
        with self.sources.open_copy(path) as copy:
            shutil.copyfile(copy, folder / name)
        self.open_file(folder / name)
        return name

    def forget_previews(self, path: str | None = None) -> None:
        """The pages kept for readers of the document read from `path` let go -- of every
        document, without one."""
        prefix = f"{self.doc_id(path)}-" if path else ""
        for kept in self.preview_dir.glob(f"{prefix}*.png"):
            kept.unlink(missing_ok=True)

    def _viewed(self, doc: str, path: Path) -> None:
        with self.viewing:
            self.viewed[doc] = (path, time.monotonic())
            if self._watching is None or not self._watching.is_alive():
                self._watching = threading.Thread(target=self._give_back_unviewed, daemon=True)
                self._watching.start()

    def _give_back_unviewed(self) -> None:
        """The files downloaded to be looked at, given back once not looked at for
        `PREVIEW_HOLD_SECONDS`; ends when none is left to watch."""
        while True:
            time.sleep(state.GIVE_BACK_TICK)
            with self.viewing:
                due = [doc for doc, (_, at) in self.viewed.items()
                       if time.monotonic() - at >= state.PREVIEW_HOLD_SECONDS]
                paths = [self.viewed.pop(doc)[0] for doc in due]
                left = bool(self.viewed)
            try:
                state._give_back_for_good(paths)
            except Exception:  # noqa: BLE001 -- the file stays here
                traceback.print_exc(file=sys.stderr)
            if not left:
                return

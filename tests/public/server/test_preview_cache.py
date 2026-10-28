"""A page drawn for a reader is kept on disk, under a limit, so looking at it again needs no file
-- a cloud drive's file is downloaded to be drawn. What was online only is given back once it has
not been looked at for a while, and a download of files is given back once zipped. Invented PDF."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz
import pytest

from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.domain.enums import DocumentFormat
from graphme.server import state
from graphme.server.state import Demo
from tests.public.server.conftest import SETTINGS, FakeSpace, until


class _Counted(PdfPageRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.drawn: list[int] = []

    def preview(self, source: Path, document_format: DocumentFormat, index: int) -> bytes:
        self.drawn.append(index)
        return super().preview(source, document_format, index)


def _pdf(path: Path, pages: int = 3) -> Path:
    document = fitz.open()
    for number in range(pages):
        document.new_page().insert_text((72, 72), f"Invented page {number + 1}")
    document.save(path)
    return path


def _demo(tmp_path: Path, renderer: Any) -> Demo:
    settings = SETTINGS.model_copy(update={"data_dir": tmp_path / "data"})
    made = Demo(settings, renderer=renderer, root=tmp_path / "server")
    made.graph._space = FakeSpace()  # type: ignore[assignment]
    return made


def test_a_page_looked_at_once_is_shown_again_with_no_file_even_after_a_restart(
    tmp_path: Path,
) -> None:
    source = _pdf(tmp_path / "invented.pdf")
    first = _Counted()
    demo = _demo(tmp_path, first)
    doc = demo.open_path(str(source))

    shown = demo.preview(doc, 2)
    again = _demo(tmp_path, _Counted())  # the app started again
    kept = again.preview(again.open_path(str(source)), 2)

    assert shown.startswith(b"\x89PNG") and kept == shown
    assert first.drawn == [2] and again.renderer.drawn == []  # type: ignore[attr-defined]


def test_the_pages_kept_stay_under_the_limit_the_oldest_going_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pdf(tmp_path / "invented.pdf")
    demo = _demo(tmp_path, _Counted())
    doc = demo.open_path(str(source))
    one = demo.preview(doc, 1)
    monkeypatch.setattr(state, "preview_limit", lambda settings: len(one) * 3 // 2)  # one fits

    demo.preview(doc, 2)

    kept = sorted(p.name for p in (demo.root / "previews").iterdir())
    assert len(kept) == 1 and kept[0].endswith("-2.png")


def test_a_file_downloaded_to_be_looked_at_is_given_back_once_not_looked_at_for_a_while(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clouded, mine = _pdf(tmp_path / "online.pdf"), _pdf(tmp_path / "kept here.pdf")
    given_back: list[Path] = []
    monkeypatch.setattr(state, "online_only", lambda path: Path(path) == clouded)
    monkeypatch.setattr(state, "evict", lambda paths: given_back.extend(paths) or len(paths))
    monkeypatch.setattr(state, "PREVIEW_HOLD_SECONDS", 0.2)
    monkeypatch.setattr(state, "GIVE_BACK_TICK", 0.05)
    monkeypatch.setattr(state, "SETTLE_SECONDS", ())
    demo = _demo(tmp_path, _Counted())

    demo.preview(demo.open_path(str(clouded)), 1)
    demo.preview(demo.open_path(str(mine)), 1)

    assert until(lambda: given_back == [clouded], 3)  # the user's own file left as it is


def test_a_download_of_files_gives_back_what_was_online_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clouded, mine = _pdf(tmp_path / "online.pdf"), _pdf(tmp_path / "kept here.pdf")
    given_back: list[Path] = []
    monkeypatch.setattr(state, "online_only", lambda path: Path(path) == clouded)
    monkeypatch.setattr(state, "evict", lambda paths: given_back.extend(paths) or len(paths))
    monkeypatch.setattr(state, "SETTLE_SECONDS", ())
    demo = _demo(tmp_path, _Counted())
    space = demo.graph._space
    for name, path in (("a", clouded), ("b", mine)):
        space.held[name], space.paths[name] = path.name, str(path)  # type: ignore[union-attr]

    demo.files_zip([("a", "found"), ("b", "found")])

    assert until(lambda: given_back == [clouded])


def test_a_document_deleted_takes_its_pages_kept(tmp_path: Path, client: Any = None) -> None:
    source = _pdf(tmp_path / "invented.pdf")
    demo = _demo(tmp_path, _Counted())
    doc = demo.open_path(str(source))
    demo.preview(doc, 1)
    other = _pdf(tmp_path / "other.pdf")
    demo.preview(demo.open_path(str(other)), 1)

    demo.forget_previews(str(source))

    kept = [p.name for p in (demo.root / "previews").iterdir()]
    assert len(kept) == 1 and not kept[0].startswith(doc)

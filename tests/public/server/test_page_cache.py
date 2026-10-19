"""A document opened again after a restart is not drawn again: what the renderer measured is
kept beside its page images, and used while nothing that drew it has changed. Invented PDF;
the renderer is the real PDF rasteriser, counted."""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from graphme.adapters.rendering import page_cache
from graphme.adapters.rendering.browser_office import RenderedPage
from graphme.adapters.rendering.page_renderer import PageRenderer
from graphme.adapters.rendering.pdf_pages import PdfPageRenderer
from graphme.server.state import Demo
from tests.public.server.conftest import SETTINGS as _BASE

# A key of no use to anyone: the builder is made, nothing it would call is asked.
SETTINGS = _BASE.model_copy(update={"api_key": "invented-key"})


class _Counted:
    def __init__(self) -> None:
        self.drawn = 0

    def render(self, source, document_format):  # noqa: ANN001, ANN201
        self.drawn += 1
        return PdfPageRenderer().render(source, document_format)


def _pdf(path: Path, words: str) -> Path:
    document = fitz.open()
    document.new_page().insert_text((72, 72), words)
    document.save(path)
    return path


def _opened(root: Path, source: Path, built: list[int] | None = None,
            ) -> tuple[Demo, _Counted, tuple[RenderedPage, ...]]:
    counted = _Counted()
    demo = Demo(SETTINGS, PageRenderer(browser=counted, pdf=counted), root=root)
    if built is not None:
        made = demo._builder
        demo._builder = lambda session: built.append(1) or made(session)  # type: ignore[method-assign]
    session = demo.session(demo.open_path(str(source)))
    demo._prepare(session)
    return demo, counted, session.pages


def test_opened_again_after_a_restart_it_is_not_drawn_again(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "invented.pdf", "An invented page")
    _, first, pages = _opened(tmp_path / "server", source)
    _, again, kept = _opened(tmp_path / "server", source)  # a new start, same folder
    assert (first.drawn, again.drawn) == (1, 0)
    assert kept == pages


def test_anything_that_drew_it_changed_and_it_is_drawn_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pdf(tmp_path / "invented.pdf", "An invented page")
    _opened(tmp_path / "server", source)
    monkeypatch.setattr(page_cache, "identity", lambda viewer: "other code")
    _, again, _ = _opened(tmp_path / "server", source)
    assert again.drawn == 1
    _pdf(source, "The same name, other words")
    _, changed, _ = _opened(tmp_path / "server", source)
    assert changed.drawn == 1


def test_what_the_renderer_measured_comes_back_as_it_was(tmp_path: Path) -> None:
    page = RenderedPage(index=1, width=20, height=10, png=b"\x89PNG invented", label="Sheet",
                        column_edges={1: 0, 2: 64}, row_edges={1: 0, 2: 20}, scale=1.5,
                        device_pixel_ratio=2.0, text_runs=({"id": "s1", "x": 1.0},),
                        elements=({"index": 3, "kind": "picture"},))
    (tmp_path / "page-01.png").write_bytes(page.png)
    page_cache.save(tmp_path, (page,), "key")
    assert page_cache.load(tmp_path, "key") == (page,)
    assert page_cache.load(tmp_path, "other key") is None


def test_after_a_restart_its_graph_as_bound_is_kept_too(tmp_path: Path) -> None:
    # Reading a workbook again and cutting its crops again took longer than the drawing kept.
    source = _pdf(tmp_path / "invented.pdf", "An invented page")
    first, second = [], []
    demo, _, _ = _opened(tmp_path / "server", source, first)
    bound = next(iter(demo.sessions.values())).bound
    again, _, _ = _opened(tmp_path / "server", source, second)
    assert (len(first), len(second)) == (1, 0)
    assert next(iter(again.sessions.values())).bound == bound


def test_a_kept_graph_whose_pictures_are_gone_is_built_again(tmp_path: Path) -> None:
    from graphme.adapters.storage.assets import LocalAssetStore

    store = LocalAssetStore(tmp_path / "assets")
    held = store.put(b"invented crop", ".png")
    text = f'{{"nodes": [{{"metadata": {{"crop_asset_hash": "{held}"}}}}], "asset_hash": "{held}"}}'
    assert page_cache.assets_present(text, store)
    assert not page_cache.assets_present(text.replace(held, "f" * 64), store)

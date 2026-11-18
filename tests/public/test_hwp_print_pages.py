"""A Hangul print in a real browser: every page keeps what it draws. The renderer is a stand-in
that draws two invented pages; Chrome (or Playwright's Chromium) must be on this machine."""
from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from graphme.adapters.rendering import hwp_print
from graphme.adapters.rendering.browser_capture import NO_BROWSER
from graphme.config import Settings

# Each page is an SVG of its own, and the renderer numbers its clip paths per page: a portrait
# page and a landscape one both name `cell-clip-1`, each the box of its own cell.
RENDERER = """
const PAGES = [
  `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600">
     <defs><clipPath id="cell-clip-1">
       <rect x="20" y="20" width="100" height="40"/></clipPath></defs>
     <text x="30" y="45" font-size="12" clip-path="url(#cell-clip-1)">Portrait cell</text>
   </svg>`,
  `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400">
     <defs><clipPath id="cell-clip-1">
       <rect x="420" y="200" width="160" height="40"/></clipPath></defs>
     <text x="430" y="225" font-size="12" clip-path="url(#cell-clip-1)">Landscape cell</text>
   </svg>`,
];
export default async function init() {}
export class HwpDocument {
  constructor(bytes) {}
  pageCount() { return PAGES.length; }
  renderPageSvg(index) { return PAGES[index]; }
}
"""


def test_a_page_s_cells_are_clipped_by_its_own_boxes_not_another_page_s(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "hwp").mkdir(parents=True)
    (bundle / "hwp" / "rhwp.js").write_text(RENDERER, encoding="utf-8")
    (bundle / "hwp" / "rhwp_bg.wasm").write_bytes(b"")
    source = tmp_path / "invented.hwpx"
    source.write_bytes(b"invented")
    out = tmp_path / "invented.pdf"

    try:
        hwp_print.print_pdf(source, out, bundle=bundle,
                            browser_path=Settings().chrome_executable)
    except RuntimeError as error:
        if str(error) == NO_BROWSER:
            pytest.skip("no browser on this machine")
        raise

    with pymupdf.open(out) as document:  # type: ignore[no-untyped-call]
        pages = [page.get_text() for page in document]
    assert "Portrait cell" in pages[0]
    assert "Landscape cell" in pages[1]

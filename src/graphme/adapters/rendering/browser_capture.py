"""Render one OOXML document to page images: the viewer bundle in a browser, through Playwright.

    python -m graphme.adapters.rendering.browser_capture KIND SOURCE WORKSPACE WIDTH DPR

Run in a process of its own so the caller's timeout can stop it. DIST_ROOT names the viewer
bundle; BROWSER_PATH, when set, the Chrome to draw with (else Playwright's own browser).

Every render serves a site of its own: its document, page and plan, with the bundle read under
them. Copying the document into the shared bundle let two renders at once overwrite each
other's input. One handler reads both places, so nothing is linked -- Windows will not make a
symbolic link without admin rights -- and no second Python process is started.
"""
from __future__ import annotations

import base64
import functools
import json
import os
import shutil
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from playwright.sync_api import ConsoleMessage

PAGE = Path(__file__).with_name("browser_render.html")
OWN = frozenset({"render.html", "plan.json"})
"""A render's own files, never taken from the bundle when this render has none."""
TYPES = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".wasm": "application/wasm", ".css": "text/css",
    ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
}
"""Named here: Windows reads types from the registry, where .js can be text/plain, and a
browser refuses a module script served so."""
FONTS = Path(__file__).resolve().parents[2] / "fonts"


def font_environment(workspace: Path, *, platform: str = sys.platform) -> dict[str, str] | None:
    """Chrome on Linux finds fonts through fontconfig, which knows only what the system
    installed -- under WSL not even Windows' -- so Korean pages drew as boxes wherever no
    Korean font had been installed. The bundled Hangul font is added beside the system's."""
    if not platform.startswith("linux"):
        return None
    system = os.environ.get("FONTCONFIG_FILE", "/etc/fonts/fonts.conf")
    cache = Path.home() / ".cache" / "graphme" / "fontconfig"
    conf = workspace / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
        f'  <include ignore_missing="yes">{escape(system)}</include>\n'
        f"  <dir>{escape(str(FONTS))}</dir>\n"
        f"  <cachedir>{escape(str(cache))}</cachedir>\n</fontconfig>\n",
        encoding="utf-8")
    return {**os.environ, "FONTCONFIG_FILE": str(conf)}


NO_BROWSER = ("no browser to draw pages with: install Google Chrome, or run "
              "`python -m playwright install chromium --only-shell`")


class SiteHandler(SimpleHTTPRequestHandler):
    """The render's own files first, the viewer bundle under them."""

    def __init__(self, *args: Any, site: Path, bundle: Path, **kwargs: Any) -> None:
        self.bundle = bundle
        super().__init__(*args, directory=str(site), **kwargs)

    def translate_path(self, path: str) -> str:
        own = Path(super().translate_path(path))
        relative = own.relative_to(Path(str(self.directory)))
        first = relative.parts[0] if relative.parts else ""
        if own.exists() or first in OWN or first.startswith("sample."):
            return str(own)
        return str(self.bundle / relative)

    def guess_type(self, path: str | os.PathLike[str]) -> str:
        return TYPES.get(Path(path).suffix.lower()) or super().guess_type(path)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- the base's name
        pass


def _say_warnings(message: ConsoleMessage) -> None:
    if message.type == "warning":
        print(message.text, file=sys.stderr)


def capture(kind: str, source: Path, workspace: Path, *, width: int, dpr: float,
            bundle: Path, browser_path: str | None) -> None:
    """Draw `source` and write each page as `page-NNN.png`, with `manifest.json` beside them."""
    from playwright.sync_api import Error, sync_playwright

    part = bundle / ("xlsx" if kind == "xlsx" else "dist")
    if not part.is_dir():  # otherwise the page only says a module failed to load
        raise RuntimeError(
            f"뷰어 번들이 {bundle}에 없습니다 ({part.name}/) -- 설치 프로그램을 다시 실행하거나 "
            "src/graphme/adapters/rendering/viewer/setup.sh로 만들고, 다른 곳에 있다면 "
            "GRAPHME_VIEWER_DIR로 가리키세요.")
    site = workspace / "site"
    site.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, site / f"sample.{kind}")
    shutil.copyfile(PAGE, site / "render.html")
    if (workspace / "plan.json").is_file():  # worksheets only; other formats paginate
        shutil.copyfile(workspace / "plan.json", site / "plan.json")
    handler = functools.partial(SiteHandler, site=site.resolve(), bundle=bundle.resolve())
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(
                        headless=True, executable_path=browser_path or None,
                        env=cast("dict[str, str | float | bool] | None",
                                 font_environment(workspace)))
                except Error as error:
                    if "xecutable doesn't exist" in str(error) or "ENOENT" in str(error):
                        raise RuntimeError(NO_BROWSER) from None
                    raise
                try:
                    page = browser.new_page()
                    # What the page chose to leave out (a sheet past the viewer's limits).
                    page.on("console", _say_warnings)
                    page.goto(f"{origin}/render.html?kind={kind}&width={width}&dpr={dpr}",
                              wait_until="domcontentloaded", timeout=120_000)
                    page.wait_for_function("() => window.__ready === true", timeout=300_000)
                    failure = page.evaluate("() => window.__error || null")
                    if failure:
                        raise RuntimeError(failure)
                    pages: list[dict[str, Any]] = page.evaluate("() => window.__pages")
                finally:
                    browser.close()
        finally:
            server.shutdown()
    for entry in pages:
        data = entry.pop("dataUrl").split(",", 1)[1]
        (workspace / f"page-{entry['index'] + 1:03d}.png").write_bytes(base64.b64decode(data))
    (workspace / "manifest.json").write_text(
        json.dumps({"kind": kind, "pages": pages}, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    """The error alone on stderr: the caller shows its first lines, and a traceback's are not
    the message."""
    try:
        kind, source, workspace, width, dpr = argv
        capture(kind, Path(source), Path(workspace), width=int(width) or 1400,
                dpr=float(dpr) or 2.0, bundle=Path(os.environ.get("DIST_ROOT", ".")),
                browser_path=os.environ.get("BROWSER_PATH"))
    except Exception as error:  # noqa: BLE001 -- reported to the parent, which raises it
        print(str(error) or type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

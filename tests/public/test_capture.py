"""The page capture: the viewer site served without links, and a missing browser said plainly.
Invented files only; the browser itself is not started except to fail to start."""
from __future__ import annotations

import functools
import mimetypes
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from graphme.adapters.rendering import browser_capture
from graphme.config import chrome_paths


def _serve(site: Path, bundle: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(browser_capture.SiteHandler, site=site, bundle=bundle)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _get(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.headers["Content-Type"], response.read()
    except urllib.error.HTTPError as error:
        return error.code, "", b""


def test_the_render_s_own_files_come_first_and_the_bundle_under_them(tmp_path: Path) -> None:
    site, bundle = tmp_path / "site", tmp_path / "bundle"
    (site).mkdir()
    (bundle / "dist").mkdir(parents=True)
    (site / "render.html").write_text("own page")
    (bundle / "render.html").write_text("bundle page")
    (bundle / "dist" / "viewer.js").write_text("viewer")
    (bundle / "plan.json").write_text("a stale plan")
    server, origin = _serve(site, bundle)
    try:
        assert _get(f"{origin}/render.html")[2] == b"own page"
        assert _get(f"{origin}/dist/viewer.js")[2] == b"viewer"
        # what a render owns never falls through to the bundle: no plan here means none
        assert _get(f"{origin}/plan.json")[0] == 404
        assert _get(f"{origin}/../bundle/render.html")[2] != b"bundle page"
    finally:
        server.shutdown()


def test_module_scripts_and_wasm_are_typed_whatever_the_system_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows reads these from the registry, where .js can be text/plain
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    monkeypatch.setitem(mimetypes.types_map, ".mjs", "text/plain")
    site, bundle = tmp_path / "site", tmp_path / "bundle"
    site.mkdir()
    bundle.mkdir()
    for name in ("a.js", "b.mjs", "c.wasm"):
        (bundle / name).write_bytes(b"x")
    server, origin = _serve(site, bundle)
    try:
        assert _get(f"{origin}/a.js")[1].startswith("text/javascript")
        assert _get(f"{origin}/b.mjs")[1].startswith("text/javascript")
        assert _get(f"{origin}/c.wasm")[1] == "application/wasm"
    finally:
        server.shutdown()


def test_no_browser_to_start_is_said_with_what_to_install(tmp_path: Path) -> None:
    source = tmp_path / "invented.docx"
    source.write_bytes(b"not read")
    with pytest.raises(RuntimeError, match="playwright install chromium"):
        browser_capture.capture("docx", source, tmp_path / "work", width=800, dpr=1,
                                bundle=tmp_path, browser_path=str(tmp_path / "no-chrome"))


def test_main_reports_the_error_alone_and_fails(tmp_path: Path, capsys) -> None:
    code = browser_capture.main(["docx", str(tmp_path / "missing.docx"), str(tmp_path), "800", "1"])
    assert code == 1
    assert "Traceback" not in capsys.readouterr().err


def test_chrome_is_looked_for_where_windows_installs_it() -> None:
    paths = chrome_paths({
        "PROGRAMFILES": r"C:\Program Files", "LOCALAPPDATA": r"C:\Users\a\AppData\Local"})
    assert r"C:\Program Files\Google\Chrome\Application\chrome.exe" in paths
    assert r"C:\Users\a\AppData\Local\Google\Chrome\Application\chrome.exe" in paths
    assert "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in paths


def test_chrome_is_looked_for_in_the_user_s_own_applications_too() -> None:
    assert "/Users/a/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in chrome_paths(
        {"HOME": "/Users/a"})


def test_an_old_playwright_dir_setting_is_ignored() -> None:
    from graphme.config import load_settings

    settings = load_settings({"GRAPHME_PROFILE": "test", "GRAPHME_PLAYWRIGHT_DIR": "frontend"})
    assert not hasattr(settings, "playwright_directory")

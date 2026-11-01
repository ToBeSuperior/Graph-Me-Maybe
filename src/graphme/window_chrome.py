"""On Windows the app's header is the window's title bar, as in Claude's or VS Code's window.

Windows draws a light title bar over a dark app whenever Windows itself is set to light, and it
cannot be coloured from the page. So the title bar is taken away and the header takes its place:

- WebView2 is told to treat the page's `app-region: drag` parts as the title bar
  (`IsNonClientRegionSupportEnabled`): dragging them moves the window with Windows' own move,
  so it snaps to the screen's edges, and a double click maximizes.
- The window's caption is removed by answering `WM_NCCALCSIZE` with the caption left out; the
  left, right and bottom frames stay, so the window still resizes and keeps its shadow.
- The page draws minimize, maximize and close (`ChromeApi`).

Nothing changes unless the WebView2 setting took: on a runtime too old for it the window keeps
its own title bar, and the page draws no buttons. GRAPHME_NATIVE_TITLE_BAR=1 keeps it always.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

NATIVE_ENV = "GRAPHME_NATIVE_TITLE_BAR"
_log = logging.getLogger(__name__)
_KEEP: list[Any] = []  # the window procedure handed to Windows lives as long as the process


def wanted(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> bool:
    """Whether the window draws its own title bar: Windows only, unless asked not to."""
    env = os.environ if env is None else env
    return platform == "win32" and env.get(NATIVE_ENV, "").strip() not in ("1", "true", "yes")


def client_top(window_top: int, *, maximized: bool, frame: int) -> int:
    """Where the page starts once the caption is gone. A maximized window hangs its frame off
    the top of the screen, so the page starts that much lower or its top is cut off."""
    return window_top + frame if maximized else window_top


class ChromeApi:
    """What the page's title bar asks of the window (pywebview's `js_api`)."""

    def __init__(self) -> None:
        self.window: Any = None
        self.custom = False
        self.zoomed = False

    def chrome(self) -> bool:
        """True once the window's own title bar is gone and the page must draw the buttons."""
        return self.custom

    def minimize(self) -> None:
        self.window.minimize()

    def toggle_maximize(self) -> bool:
        if self.zoomed:
            self.window.restore()
        else:
            self.window.maximize()
        return not self.zoomed

    def maximized(self) -> bool:
        return self.zoomed

    def close(self) -> None:
        self.window.destroy()

    def attach(self, window: Any) -> None:
        """Follow the window's state, however it changed (the button, a double click, a snap)."""
        self.window = window
        window.events.maximized += lambda *_: setattr(self, "zoomed", True)
        window.events.restored += lambda *_: setattr(self, "zoomed", False)


def install(api: ChromeApi) -> None:  # pragma: no cover -- Windows and WebView2 only
    """Hook pywebview's WebView2 start: the setting first, then the caption, on the UI thread
    the window belongs to, before the page is first loaded."""
    from webview.platforms import edgechromium  # type: ignore[import-not-found,unused-ignore]

    original = edgechromium.EdgeChrome.on_webview_ready

    def ready(self: Any, sender: Any, args: Any) -> Any:
        try:
            if args.IsSuccess:
                sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
                _hide_caption(int(self.form.Handle.ToInt64()))
                api.custom = True
        except Exception:  # noqa: BLE001 -- an old runtime: the window keeps its title bar
            _log.exception("the window keeps its own title bar")
        return original(self, sender, args)  # type: ignore[no-untyped-call,unused-ignore]

    edgechromium.EdgeChrome.on_webview_ready = ready  # type: ignore[method-assign,unused-ignore]


def _hide_caption(hwnd: int) -> None:  # pragma: no cover -- Windows only
    import ctypes
    from ctypes import wintypes

    user32, comctl32 = ctypes.windll.user32, ctypes.windll.comctl32  # type: ignore[attr-defined]
    lresult = ctypes.c_ssize_t
    proc_type = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ctypes.c_size_t, ctypes.c_size_t)
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                         wintypes.LPARAM]
    comctl32.DefSubclassProc.restype = lresult
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, proc_type, ctypes.c_size_t,
                                           ctypes.c_size_t]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]

    class Params(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    wm_nccalcsize, sm_cyframe, sm_cxpaddedborder = 0x0083, 33, 92

    def proc(handle: int, message: int, wparam: int, lparam: int, _id: int, _ref: int) -> int:
        if message == wm_nccalcsize and wparam:
            params = ctypes.cast(lparam, ctypes.POINTER(Params)).contents
            top = params.rgrc[0].top
            done = comctl32.DefSubclassProc(handle, message, wparam, lparam)
            dpi = user32.GetDpiForWindow(handle)
            frame = (user32.GetSystemMetricsForDpi(sm_cyframe, dpi)
                     + user32.GetSystemMetricsForDpi(sm_cxpaddedborder, dpi))
            params.rgrc[0].top = client_top(top, maximized=bool(user32.IsZoomed(handle)),
                                            frame=frame)
            return int(done)
        return int(comctl32.DefSubclassProc(handle, message, wparam, lparam))

    callback = proc_type(proc)
    _KEEP.append(callback)
    if not comctl32.SetWindowSubclass(hwnd, callback, 1, 0):
        raise OSError("the window procedure could not be replaced")
    # FRAMECHANGED, and nothing else moved: NOSIZE, NOMOVE, NOZORDER, NOACTIVATE
    swp_frame = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, swp_frame)

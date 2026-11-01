"""On Windows the app's header is the window's title bar, as in Claude's or VS Code's window.

Windows draws a light title bar over a dark app whenever Windows itself is set to light, and it
cannot be coloured from the page. So the window is opened with no frame and the header takes the
title bar's place, the way Electron's frameless windows do:

- WebView2 is told to treat the page's `app-region: drag` parts as the title bar
  (`IsNonClientRegionSupportEnabled`): dragging them moves the window with Windows' own move,
  so it snaps to the screen's edges, and a double click maximizes.
- The window keeps the styles a captioned window has (`WS_OVERLAPPEDWINDOW`: snapping,
  maximizing to the work area, the taskbar's minimize), but `WM_NCCALCSIZE` hands the whole of
  it to the page. A frame kept beside the page is a white line on Windows 10, whose border
  colour cannot be set; and a form that still counted a caption grew by one at every restore.
- With no frame left to grab, thin grips over the page's left, right and bottom edges resize it
  (`edge_grips`). The page itself is drawn by another process, which a hit test does not pass
  through, so the grips are windows of this thread laid over it.
- The page draws minimize, maximize and close (`ChromeApi`).

Nothing changes unless the WebView2 setting took: on a runtime too old for it the window gets
its frame and title bar back, and the page draws no buttons. GRAPHME_NATIVE_TITLE_BAR=1 keeps
them always.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

NATIVE_ENV = "GRAPHME_NATIVE_TITLE_BAR"
GRIP = 6  # how far in from an edge it resizes, at 96 dpi
HT = {"left": 10, "right": 11, "bottom": 15, "bottomleft": 16, "bottomright": 17}
_log = logging.getLogger(__name__)
_KEEP: list[Any] = []  # what is handed to Windows lives as long as the process


def wanted(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> bool:
    """Whether the window draws its own title bar: Windows only, unless asked not to."""
    env = os.environ if env is None else env
    return platform == "win32" and env.get(NATIVE_ENV, "").strip() not in ("1", "true", "yes")


def client_area(window: tuple[int, int, int, int], *, maximized: bool,
                frame: int) -> tuple[int, int, int, int]:
    """The page's rectangle in a window of `window`'s: all of it. A maximized window hangs its
    frame off every side of the screen, so the page is taken in by that much."""
    left, top, right, bottom = window
    if not maximized:
        return window
    return left + frame, top + frame, right - frame, bottom - frame


def edge_grips(width: int, height: int, *, grip: int) -> list[tuple[int, int, int, int, int]]:
    """Where the window is resized from, as (hit test, x, y, width, height) in its own pixels:
    its sides and bottom, and the bottom corners. The top is the header, which moves it."""
    side = height - grip
    return [
        (HT["left"], 0, 0, grip, side),
        (HT["right"], width - grip, 0, grip, side),
        (HT["bottom"], grip, side, width - 2 * grip, grip),
        (HT["bottomleft"], 0, side, grip, grip),
        (HT["bottomright"], width - grip, side, grip, grip),
    ]


class ChromeApi:
    """What the page's title bar asks of the window (pywebview's `js_api`).

    pywebview hands the page every public attribute, walking into objects; what is not for the
    page starts with `_`. A window held as `window` was walked into its .NET form
    (`native.AccessibilityObject.Bounds.Empty...` without end), off the form's thread, and the
    window stopped answering before the page could ask for anything."""

    def __init__(self) -> None:
        self._window: Any = None
        self._custom = False
        self._zoomed = False

    def chrome(self) -> bool:
        """True once the window's own title bar is gone and the page must draw the buttons."""
        return self._custom

    def minimize(self) -> None:
        self._window.minimize()

    def toggle_maximize(self) -> bool:
        if self._zoomed:
            self._window.restore()
        else:
            self._window.maximize()
        return not self._zoomed

    def maximized(self) -> bool:
        return self._zoomed

    def close(self) -> None:
        self._window.destroy()

    def attach(self, window: Any) -> None:
        """Follow the window's state, however it changed (the button, a double click, a snap)."""
        self._window = window
        window.events.maximized += lambda *_: setattr(self, "_zoomed", True)
        window.events.restored += lambda *_: setattr(self, "_zoomed", False)


def install(api: ChromeApi) -> None:  # pragma: no cover -- Windows and WebView2 only
    """Hook pywebview's WebView2 start: the setting first, then the frame, on the UI thread
    the window belongs to, before the page is first loaded."""
    from webview.platforms import edgechromium  # type: ignore[import-not-found,unused-ignore]

    original = edgechromium.EdgeChrome.on_webview_ready

    def ready(self: Any, sender: Any, args: Any) -> Any:
        _wear_icon(self.form)
        try:
            if args.IsSuccess:
                sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
                _take_frame(int(self.form.Handle.ToInt64()))
                api._custom = True
        except Exception:  # noqa: BLE001 -- an old runtime: the window gets its frame back
            _log.exception("the window keeps its own title bar")
            _give_frame_back(self.form)
        return original(self, sender, args)  # type: ignore[no-untyped-call,unused-ignore]

    edgechromium.EdgeChrome.on_webview_ready = ready  # type: ignore[method-assign,unused-ignore]


def _wear_icon(form: Any) -> None:  # pragma: no cover -- Windows only
    """The app's icon on the window, and so on the taskbar: uv's executable carries a generic
    one."""
    try:
        from pathlib import Path

        from System.Drawing import Icon  # type: ignore[import-not-found,unused-ignore]

        form.Icon = Icon(str(Path(__file__).parent / "icons" / "AppIcon.ico"))
    except Exception:  # noqa: BLE001 -- the generic icon, then
        _log.exception("the window keeps the executable's icon")


def _give_frame_back(form: Any) -> None:  # pragma: no cover -- Windows only
    try:
        from System.Windows.Forms import (  # type: ignore[import-not-found,unused-ignore]
            FormBorderStyle,
        )

        form.FormBorderStyle = FormBorderStyle.Sizable
    except Exception:  # noqa: BLE001
        _log.exception("the window could not get its frame back")


def _take_frame(hwnd: int) -> None:  # pragma: no cover -- Windows only
    import ctypes
    from ctypes import wintypes

    user32, comctl32 = ctypes.windll.user32, ctypes.windll.comctl32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    lresult = ctypes.c_ssize_t
    subclass_type = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ctypes.c_size_t, ctypes.c_size_t)
    wndproc_type = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                         wintypes.LPARAM]
    comctl32.DefSubclassProc.restype = lresult
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, subclass_type, ctypes.c_size_t,
                                           ctypes.c_size_t]
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM]
    user32.DefWindowProcW.restype = lresult
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    user32.SendMessageW.restype = lresult
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
    user32.LoadCursorW.restype = wintypes.HANDLE

    class Params(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    class StyleStruct(ctypes.Structure):
        _fields_ = [("old", wintypes.DWORD), ("new", wintypes.DWORD)]

    class WndClass(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("proc", wndproc_type), ("cls", ctypes.c_int),
                    ("wnd", ctypes.c_int), ("instance", wintypes.HINSTANCE),
                    ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
                    ("brush", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR),
                    ("name", wintypes.LPCWSTR)]

    gwl_style, ws_overlappedwindow = -16, 0x00CF0000
    wm_size, wm_stylechanging, wm_nccalcsize, wm_nchittest = 0x0005, 0x007C, 0x0083, 0x0084
    wm_ncactivate, wm_nclbuttondown, wm_erasebkgnd = 0x0086, 0x00A1, 0x0014
    sm_cxframe, sm_cxpaddedborder = 32, 92
    grips: dict[int, int] = {}  # a grip's window -> its hit test

    def frame() -> int:
        dpi = user32.GetDpiForWindow(hwnd)
        return int(user32.GetSystemMetricsForDpi(sm_cxframe, dpi)
                   + user32.GetSystemMetricsForDpi(sm_cxpaddedborder, dpi))

    def place(width: int, height: int) -> None:
        zoomed = bool(user32.IsZoomed(hwnd))
        grip = GRIP * user32.GetDpiForWindow(hwnd) // 96
        for (handle, _hit), (_, x, y, w, h) in zip(
                grips.items(), edge_grips(width, height, grip=grip), strict=True):
            # on top of the page, which WebView2 may have put back above them; hidden when
            # maximized. NOACTIVATE, and SHOWWINDOW or HIDEWINDOW
            user32.SetWindowPos(handle, None, x, y, max(w, 1), max(h, 1),
                                0x0010 | (0x0080 if zoomed else 0x0040))

    def grip_proc(handle: int, message: int, wparam: int, lparam: int) -> int:
        if message == wm_nchittest:
            return grips.get(handle, 1)  # the resize cursor over it, from Windows
        if message == wm_nclbuttondown:  # the window resizes, not the grip
            user32.ReleaseCapture()
            return int(user32.SendMessageW(hwnd, wm_nclbuttondown, wparam, lparam))
        if message == wm_erasebkgnd:
            return 1
        return int(user32.DefWindowProcW(handle, message, wparam, lparam))

    def proc(handle: int, message: int, wparam: int, lparam: int, _id: int, _ref: int) -> int:
        if message == wm_nccalcsize and wparam:
            params = ctypes.cast(lparam, ctypes.POINTER(Params)).contents
            rect = params.rgrc[0]
            (rect.left, rect.top, rect.right, rect.bottom) = client_area(
                (rect.left, rect.top, rect.right, rect.bottom),
                maximized=bool(user32.IsZoomed(handle)), frame=frame())
            return 0
        if message == wm_ncactivate:  # nothing of a frame painted on activating
            return int(comctl32.DefSubclassProc(handle, message, wparam, -1))
        if message == wm_stylechanging and ctypes.c_ssize_t(wparam).value == gwl_style:
            # Windows Forms writes its frameless style back now and then: keep the window's
            styles = ctypes.cast(lparam, ctypes.POINTER(StyleStruct)).contents
            styles.new |= ws_overlappedwindow
        if message == wm_size:
            place(lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
        return int(comctl32.DefSubclassProc(handle, message, wparam, lparam))

    instance = kernel32.GetModuleHandleW(None)
    grip_callback = wndproc_type(grip_proc)
    callback = subclass_type(proc)
    _KEEP.extend([grip_callback, callback])
    wndclass = WndClass(proc=grip_callback, instance=instance, name="GraphMeMaybeGrip")
    if not user32.RegisterClassW(ctypes.byref(wndclass)) and kernel32.GetLastError() != 1410:
        raise OSError("the grips' window class could not be made")
    # WS_EX_LAYERED | WS_EX_NOACTIVATE; WS_CHILD | WS_CLIPSIBLINGS. Alpha 1 of 255: not seen,
    # but still under the pointer (a fully transparent window is not).
    for hit in HT.values():
        handle = user32.CreateWindowExW(0x00080000 | 0x08000000, "GraphMeMaybeGrip", None,
                                        0x40000000 | 0x04000000, 0, 0, 1, 1, hwnd, None,
                                        instance, None)
        if not handle:
            raise OSError("a resize grip could not be made")
        user32.SetLayeredWindowAttributes(handle, 0, 1, 0x2)  # LWA_ALPHA
        grips[int(handle)] = hit
    if not comctl32.SetWindowSubclass(hwnd, callback, 1, 0):
        raise OSError("the window procedure could not be replaced")
    user32.SetWindowLongW(hwnd, gwl_style, user32.GetWindowLongW(hwnd, gwl_style)
                          | ws_overlappedwindow)
    # FRAMECHANGED, and nothing else moved: NOSIZE, NOMOVE, NOZORDER, NOACTIVATE
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)
    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    place(client.right, client.bottom)

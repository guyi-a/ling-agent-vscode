"""Native blue control border for computer-use sessions."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Any, Callable

from ..types import Rect
from . import win32

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

WM_DESTROY = 0x0002
WM_DISPLAYCHANGE = 0x007E
WM_DPICHANGED = 0x02E0
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_PAINT = 0x000F
WM_APP = 0x8000
WM_REBUILD = WM_APP + 1
WM_PHYSICAL_ESC = WM_APP + 2

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
LLKHF_INJECTED = 0x00000010

DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
TRANSPARENT = 1
DEFAULT_GUI_FONT = 17

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
LWA_ALPHA = 0x00000002
WDA_EXCLUDEFROMCAPTURE = 0x00000011

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_BACK = 0x08
VK_ESCAPE = 0x1B
HOTKEY_ID = 1

BORDER_COLOR = 0x00F6823B  # COLORREF for #3B82F6
BORDER_ALPHA = 255
HALO_COLOR = 0x00FDC593  # COLORREF for #93C5FD
HALO_ALPHA = 76
HUD_COLOR = 0x0037291F  # COLORREF for #1F2937
HUD_ALPHA = 232
HALO_THICKNESS = 18
HUD_WIDTH = 420
HUD_HEIGHT = 76
HUD_TOP = 24


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", wintypes.BYTE * 32),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ctypes.c_ushort
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.RECT),
]
user32.GetClientRect.restype = wintypes.BOOL
user32.InvalidateRect.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.RECT),
    wintypes.BOOL,
]
user32.InvalidateRect.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    wintypes.BYTE,
    wintypes.DWORD,
]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.BeginPaint.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(PAINTSTRUCT),
]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(PAINTSTRUCT),
]
user32.EndPaint.restype = wintypes.BOOL
user32.DrawTextW.argtypes = [
    wintypes.HDC,
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wintypes.RECT),
    wintypes.UINT,
]
user32.DrawTextW.restype = ctypes.c_int
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HANDLE
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HANDLE
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
gdi32.SetTextColor.restype = wintypes.DWORD
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HANDLE
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL


def _strip_rects(
    bounds: Rect,
    dpi: int,
    thickness: int = 5,
) -> tuple[tuple[int, int, int, int], ...]:
    width = max(1, bounds.width)
    height = max(1, bounds.height)
    border = max(3, round(thickness * dpi / 96))
    return (
        (bounds.left, bounds.top, width, border),
        (
            bounds.left,
            bounds.top + height - border,
            width,
            border,
        ),
        (bounds.left, bounds.top, border, height),
        (
            bounds.left + width - border,
            bounds.top,
            border,
            height,
        ),
    )


def _is_physical_escape(
    n_code: int,
    message: int,
    vk_code: int,
    flags: int,
) -> bool:
    """Return whether a keyboard hook event is a physical Escape keydown."""

    return bool(
        n_code == HC_ACTION
        and message in (WM_KEYDOWN, WM_SYSKEYDOWN)
        and vk_code == VK_ESCAPE
        and not flags & LLKHF_INJECTED
    )


def _hud_rect(
    bounds: Rect,
    dpi: int,
) -> tuple[int, int, int, int]:
    """Return the centered top HUD rectangle for one monitor."""

    scale = dpi / 96
    width = max(1, round(HUD_WIDTH * scale))
    height = max(1, round(HUD_HEIGHT * scale))
    left = bounds.left + (bounds.width - width) // 2
    top = bounds.top + round(HUD_TOP * scale)
    return (left, top, width, height)


class ControlOverlay:
    """Own the blue border on its own Win32 message-pump thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._active = False
        self._lock = threading.Lock()
        self._hwnds: list[int] = []
        self._hud_hwnds: set[int] = set()
        self._class_names = {
            "border": "LingControlBorder",
            "halo": "LingControlHalo",
            "hud": "LingControlHud",
        }
        self._hinstance = kernel32.GetModuleHandleW(None)
        self._brushes: dict[str, int] = {}
        self._registered_classes: set[str] = set()
        self._wndproc = WNDPROC(self._window_proc)
        self._hook_proc = HOOKPROC(self._keyboard_proc)
        self._keyboard_hook: int | None = None
        self._on_stop: Callable[[], None] | None = None
        self._window_id = ""
        self._hotkey = False
        self._error: str | None = None

    @property
    def active(self) -> bool:
        return self._active

    def start(
        self,
        *,
        on_stop: Callable[[], None] | None = None,
        window_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._on_stop = on_stop
            self._window_id = window_id
            if self._thread is not None and self._thread.is_alive():
                self._active = True
                return self.status()
            self._ready.clear()
            self._error = None
            self._thread = threading.Thread(
                target=self._run,
                name="ling-control-overlay",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(2.0):
            raise RuntimeError("Control overlay did not start in time.")
        if self._error:
            raise RuntimeError(self._error)
        with self._lock:
            self._active = True
        return self.status()

    def update_window(self, window_id: str) -> None:
        with self._lock:
            self._window_id = window_id

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        with self._lock:
            self._active = False
            thread_id = self._thread_id
        if thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        if threading.current_thread() is not thread:
            thread.join(timeout=1.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
                self._thread_id = 0

    def status(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "windowId": self._window_id,
            "hotkey": self._hotkey,
            "escape": self._keyboard_hook is not None,
            "displays": [m.to_json() for m in win32.list_monitors()],
        }

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        try:
            self._register_classes()
            self._hotkey = bool(
                user32.RegisterHotKey(
                    None,
                    HOTKEY_ID,
                    MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT,
                    VK_BACK,
                )
            )
            self._keyboard_hook = int(
                user32.SetWindowsHookExW(
                    WH_KEYBOARD_LL,
                    self._hook_proc,
                    self._hinstance,
                    0,
                )
                or 0
            ) or None
            self._create_windows()
            self._ready.set()
            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(
                    ctypes.byref(message),
                    None,
                    0,
                    0,
                )
                if result == 0 or result == -1:
                    break
                if message.message == WM_QUIT:
                    break
                if message.message in (WM_HOTKEY, WM_PHYSICAL_ESC):
                    self._request_stop()
                    continue
                if message.message in (WM_DISPLAYCHANGE, WM_REBUILD):
                    self._rebuild()
                    continue
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()
        finally:
            self._destroy()

    def _request_stop(self) -> None:
        self._hide()
        callback = self._on_stop
        if callback is None:
            return
        try:
            callback()
        except Exception:
            pass

    def _keyboard_proc(
        self,
        n_code: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if n_code == HC_ACTION:
            event = ctypes.cast(
                lparam,
                ctypes.POINTER(KBDLLHOOKSTRUCT),
            ).contents
            if _is_physical_escape(
                n_code,
                int(wparam),
                int(event.vkCode),
                int(event.flags),
            ):
                if self._thread_id:
                    user32.PostThreadMessageW(
                        self._thread_id,
                        WM_PHYSICAL_ESC,
                        0,
                        0,
                    )
        return int(
            user32.CallNextHookEx(
                self._keyboard_hook,
                n_code,
                wparam,
                lparam,
            )
        )

    def _window_proc(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == WM_PAINT and hwnd in self._hud_hwnds:
            return self._paint_hud(hwnd)
        if message in (WM_DISPLAYCHANGE, WM_DPICHANGED):
            if self._thread_id:
                user32.PostThreadMessageW(
                    self._thread_id,
                    WM_REBUILD,
                    0,
                    0,
                )
            return 0
        return int(
            user32.DefWindowProcW(hwnd, message, wparam, lparam)
        )

    def _register_classes(self) -> None:
        for name, color in (
            ("border", BORDER_COLOR),
            ("halo", HALO_COLOR),
            ("hud", HUD_COLOR),
        ):
            brush = gdi32.CreateSolidBrush(color)
            if not brush:
                raise OSError(
                    ctypes.get_last_error(),
                    "CreateSolidBrush failed for control overlay",
                )
            self._brushes[name] = int(brush)
            window_class = WNDCLASSW()
            window_class.style = 0
            window_class.lpfnWndProc = self._wndproc
            window_class.cbClsExtra = 0
            window_class.cbWndExtra = 0
            window_class.hInstance = self._hinstance
            window_class.hIcon = None
            window_class.hCursor = None
            window_class.hbrBackground = brush
            window_class.lpszMenuName = None
            window_class.lpszClassName = self._class_names[name]
            atom = user32.RegisterClassW(
                ctypes.byref(window_class)
            )
            if not atom:
                raise OSError(
                    ctypes.get_last_error(),
                    f"RegisterClassW failed for {name} overlay",
                )
            self._registered_classes.add(name)

    def _paint_hud(self, hwnd: int) -> int:
        paint = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(paint))
        if not hdc:
            return 0
        old_font = None
        try:
            font = gdi32.GetStockObject(DEFAULT_GUI_FONT)
            old_font = gdi32.SelectObject(hdc, font)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            title_rect = wintypes.RECT(
                rect.left + 16,
                rect.top + 12,
                rect.right - 16,
                rect.top + 38,
            )
            hint_rect = wintypes.RECT(
                rect.left + 16,
                rect.top + 40,
                rect.right - 16,
                rect.bottom - 10,
            )
            gdi32.SetTextColor(hdc, 0x00F5EDE9)
            user32.DrawTextW(
                hdc,
                "Ling-Agent 正在接管你的电脑",
                -1,
                ctypes.byref(title_rect),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            gdi32.SetTextColor(hdc, 0x00FDC593)
            user32.DrawTextW(
                hdc,
                "按 Esc 脱离 control",
                -1,
                ctypes.byref(hint_rect),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
        finally:
            if old_font:
                gdi32.SelectObject(hdc, old_font)
            user32.EndPaint(hwnd, ctypes.byref(paint))
        return 0

    def _create_windows(self) -> None:
        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_NOACTIVATE
            | WS_EX_TOOLWINDOW
        )
        monitors = win32.list_monitors()
        for monitor in monitors:
            for left, top, strip_width, strip_height in _strip_rects(
                monitor.bounds,
                monitor.dpi,
                HALO_THICKNESS,
            ):
                self._create_overlay_window(
                    ex_style,
                    "halo",
                    left,
                    top,
                    strip_width,
                    strip_height,
                    HALO_ALPHA,
                )
            for left, top, strip_width, strip_height in _strip_rects(
                monitor.bounds,
                monitor.dpi,
                5,
            ):
                self._create_overlay_window(
                    ex_style,
                    "border",
                    left,
                    top,
                    strip_width,
                    strip_height,
                    BORDER_ALPHA,
                )
        primary = next(
            (monitor for monitor in monitors if monitor.primary),
            monitors[0] if monitors else None,
        )
        if primary is not None:
            hud_left, hud_top, hud_width, hud_height = _hud_rect(
                primary.bounds,
                primary.dpi,
            )
            hwnd = self._create_overlay_window(
                ex_style,
                "hud",
                hud_left,
                hud_top,
                hud_width,
                hud_height,
                HUD_ALPHA,
            )
            if hwnd:
                self._hud_hwnds.add(hwnd)
                user32.InvalidateRect(hwnd, None, True)
                user32.UpdateWindow(hwnd)

    def _create_overlay_window(
        self,
        ex_style: int,
        kind: str,
        left: int,
        top: int,
        width: int,
        height: int,
        alpha: int,
    ) -> int:
        hwnd = user32.CreateWindowExW(
            ex_style,
            self._class_names[kind],
            "",
            WS_POPUP,
            left,
            top,
            width,
            height,
            None,
            None,
            self._hinstance,
            None,
        )
        if not hwnd:
            return 0
        user32.SetLayeredWindowAttributes(
            hwnd,
            0,
            alpha,
            LWA_ALPHA,
        )
        try:
            user32.SetWindowDisplayAffinity(
                hwnd,
                WDA_EXCLUDEFROMCAPTURE,
            )
        except (AttributeError, OSError):
            pass
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(
            hwnd,
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOACTIVATE
            | SWP_SHOWWINDOW,
        )
        self._hwnds.append(int(hwnd))
        return int(hwnd)

    def _hide(self) -> None:
        with self._lock:
            self._active = False
        for hwnd in list(self._hwnds):
            user32.ShowWindow(hwnd, SW_HIDE)

    def _rebuild(self) -> None:
        self._destroy_windows()
        self._create_windows()

    def _destroy_windows(self) -> None:
        for hwnd in list(self._hwnds):
            user32.DestroyWindow(hwnd)
        self._hwnds.clear()
        self._hud_hwnds.clear()

    def _destroy(self) -> None:
        with self._lock:
            self._active = False
            self._hotkey = False
        try:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        except (AttributeError, OSError):
            pass
        if self._keyboard_hook:
            try:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
            except (AttributeError, OSError):
                pass
            self._keyboard_hook = None
        self._destroy_windows()
        for name in tuple(self._registered_classes):
            user32.UnregisterClassW(
                self._class_names[name],
                self._hinstance,
            )
        self._registered_classes.clear()
        for brush in self._brushes.values():
            gdi32.DeleteObject(brush)
        self._brushes.clear()

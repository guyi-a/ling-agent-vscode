"""Thin Win32 bindings for the computer-use helper."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes
from typing import Any

from ..policy import window_deny_reason
from ..types import MonitorInfo, Rect, WindowIdentity, WindowInfo

if sys.platform != "win32":
    raise RuntimeError("The computer-use helper is Windows-only.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:  # pragma: no cover - only on very old Windows
    shcore = None

WNDENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)

DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
MONITORINFOF_PRIMARY = 0x00000001
INFINITE = 0xFFFFFFFF
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    MONITORENUMPROC,
    wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MONITORINFOEXW),
]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.SetProcessDPIAware.argtypes = []
user32.SetProcessDPIAware.restype = wintypes.BOOL

dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
]
kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
psapi.EnumProcesses.argtypes = [
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
psapi.EnumProcesses.restype = wintypes.BOOL
kernel32.GetApplicationUserModelId.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.UINT),
    wintypes.LPWSTR,
]
kernel32.GetApplicationUserModelId.restype = wintypes.LONG

advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(
    ctypes.c_ubyte
)
advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

if shcore is not None:
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long


def set_dpi_awareness() -> None:
    """Set per-monitor DPI awareness before creating windows."""

    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    if shcore is not None:
        try:
            if shcore.SetProcessDpiAwareness(2) == 0:
                return
        except (AttributeError, OSError):
            pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def watch_parent(parent_pid: int) -> None:
    """Exit when the parent process exits."""

    if parent_pid <= 0:
        return
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
    if not handle:
        return

    def watch() -> None:
        try:
            kernel32.WaitForSingleObject(handle, INFINITE)
            os._exit(0)
        finally:
            kernel32.CloseHandle(handle)

    threading.Thread(
        target=watch,
        name="computer-parent-watch",
        daemon=True,
    ).start()


def _to_rect(value: wintypes.RECT) -> Rect:
    return Rect(
        left=int(value.left),
        top=int(value.top),
        right=int(value.right),
        bottom=int(value.bottom),
    )


def _window_text(hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _is_cloaked(hwnd: int) -> bool:
    value = wintypes.DWORD(0)
    result = dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_CLOAKED,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
    return result == 0 and bool(value.value)


def _window_bounds(hwnd: int) -> Rect:
    rect = wintypes.RECT()
    result = dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if result != 0 or rect.right <= rect.left or rect.bottom <= rect.top:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return Rect(0, 0, 0, 0)
    return _to_rect(rect)


def _integrity_level(process_handle: Any) -> str:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        process_handle,
        TOKEN_QUERY,
        ctypes.byref(token),
    ):
        return "unknown"
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            token,
            TOKEN_INTEGRITY_LEVEL,
            None,
            0,
            ctypes.byref(size),
        )
        if size.value == 0:
            return "unknown"
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_INTEGRITY_LEVEL,
            ctypes.cast(buffer, ctypes.c_void_p),
            size.value,
            ctypes.byref(size),
        ):
            return "unknown"
        label = ctypes.cast(
            buffer,
            ctypes.POINTER(TOKEN_MANDATORY_LABEL),
        ).contents
        sid = label.Label.Sid
        if not sid:
            return "unknown"
        count_pointer = advapi32.GetSidSubAuthorityCount(sid)
        if not count_pointer:
            return "unknown"
        count = int(count_pointer.contents.value)
        if count <= 0:
            return "unknown"
        rid_pointer = advapi32.GetSidSubAuthority(sid, count - 1)
        if not rid_pointer:
            return "unknown"
        rid = int(rid_pointer.contents.value)
        if rid >= 0x4000:
            return "system"
        if rid >= 0x3000:
            return "high"
        if rid >= 0x2000:
            return "medium"
        if rid >= 0x1000:
            return "low"
        return "untrusted"
    except (AttributeError, OSError, ValueError):
        return "unknown"
    finally:
        kernel32.CloseHandle(token)


def _aumid(process_handle: Any) -> str | None:
    try:
        length = wintypes.UINT(0)
        kernel32.GetApplicationUserModelId(
            process_handle,
            ctypes.byref(length),
            None,
        )
        if length.value == 0:
            return None
        buffer = ctypes.create_unicode_buffer(length.value)
        result = kernel32.GetApplicationUserModelId(
            process_handle,
            ctypes.byref(length),
            buffer,
        )
        if result != 0:
            return None
        return buffer.value or None
    except (AttributeError, OSError, ValueError):
        return None


def _process_info(
    pid: int,
) -> tuple[str, int, str, str | None]:
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return "", 0, "unknown", None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            path = buffer.value
        else:
            path = ""
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            creation_time = (
                int(created.dwHighDateTime) << 32
            ) | int(created.dwLowDateTime)
        else:
            creation_time = 0
        return (
            path,
            creation_time,
            _integrity_level(handle),
            _aumid(handle),
        )
    finally:
        kernel32.CloseHandle(handle)


def running_process_paths() -> dict[int, str]:
    """Return accessible pid -> executable paths."""

    capacity = 4096
    process_ids = (wintypes.DWORD * capacity)()
    needed = wintypes.DWORD(0)
    if not psapi.EnumProcesses(
        process_ids,
        ctypes.sizeof(process_ids),
        ctypes.byref(needed),
    ):
        return {}
    count = min(
        capacity,
        int(needed.value) // ctypes.sizeof(wintypes.DWORD),
    )
    own_pid = os.getpid()
    paths: dict[int, str] = {}
    for index in range(count):
        pid = int(process_ids[index])
        if pid <= 0 or pid == own_pid:
            continue
        path, _created, _integrity, _aumid = _process_info(pid)
        if path:
            paths[pid] = path
    return paths


def find_running_processes(exe_path: str) -> list[int]:
    """Return pids whose executable path matches exe_path."""

    normalized = os.path.normcase(os.path.abspath(exe_path))
    return [
        pid
        for pid, path in running_process_paths().items()
        if os.path.normcase(os.path.abspath(path)) == normalized
    ]


def _enumerate_windows(include_all: bool) -> list[WindowIdentity]:
    windows: list[WindowIdentity] = []
    own_pid = os.getpid()

    def callback(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == own_pid:
            return True
        visible = bool(user32.IsWindowVisible(hwnd))
        minimized = bool(user32.IsIconic(hwnd))
        exstyle = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        tool_window = bool(exstyle & WS_EX_TOOLWINDOW)
        cloaked = _is_cloaked(hwnd)
        bounds = _window_bounds(hwnd)
        if not include_all:
            if not visible or cloaked or tool_window:
                return True
            if bounds.width <= 0 or bounds.height <= 0:
                return True
        title = _window_text(hwnd)
        class_name = _class_name(hwnd)
        if not include_all and not title and not class_name:
            return True
        path, creation_time, integrity, aumid = _process_info(pid.value)
        exe_name = os.path.basename(path).lower() if path else ""
        windows.append(
            WindowIdentity(
                hwnd=int(hwnd),
                pid=int(pid.value),
                creation_time=creation_time,
                exe_path=path,
                exe_name=exe_name,
                title=title,
                class_name=class_name,
                bounds=bounds,
                visible=visible,
                cloaked=cloaked,
                minimized=minimized,
                aumid=aumid,
                integrity=integrity,
                tool_window=tool_window,
            )
        )
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return windows


def _enumerate_monitors() -> list[MonitorInfo]:
    monitors: list[MonitorInfo] = []

    def callback(
        hmonitor: int,
        _hdc: int,
        _rect: Any,
        _lparam: int,
    ) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            device = info.szDevice
            if isinstance(device, bytes):
                device = device.decode("utf-16-le", errors="replace")
            monitors.append(
                MonitorInfo(
                    index=len(monitors),
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    device=str(device),
                    bounds=_to_rect(info.rcMonitor),
                    work_area=_to_rect(info.rcWork),
                )
            )
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return monitors


def list_monitors() -> list[MonitorInfo]:
    """Return the current physical monitor layout."""

    return _enumerate_monitors()


def describe_window(hwnd: int) -> WindowIdentity | None:
    """Return the current identity of one top-level window."""

    if not user32.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    path, creation_time, integrity, aumid = _process_info(pid.value)
    exstyle = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    return WindowIdentity(
        hwnd=int(hwnd),
        pid=int(pid.value),
        creation_time=creation_time,
        exe_path=path,
        exe_name=os.path.basename(path).lower() if path else "",
        title=_window_text(hwnd),
        class_name=_class_name(hwnd),
        bounds=_window_bounds(hwnd),
        visible=bool(user32.IsWindowVisible(hwnd)),
        cloaked=_is_cloaked(hwnd),
        minimized=bool(user32.IsIconic(hwnd)),
        aumid=aumid,
        integrity=integrity,
        tool_window=bool(exstyle & WS_EX_TOOLWINDOW),
    )


def list_windows(
    *,
    include_all: bool = False,
    query: str = "",
    max_windows: int = 100,
) -> dict[str, Any]:
    """List top-level windows and displays."""

    identities = _enumerate_windows(include_all)
    needle = query.strip().casefold()
    if needle:
        identities = [
            window
            for window in identities
            if needle in window.title.casefold()
            or needle in window.exe_name.casefold()
            or needle in window.class_name.casefold()
        ]
    identities.sort(key=lambda window: (window.title.casefold(), window.pid, window.hwnd))
    limit = max(1, min(int(max_windows), 500))
    selected = identities[:limit]
    windows: list[WindowInfo] = []
    for identity in selected:
        reason = window_deny_reason(identity)
        windows.append(
            WindowInfo(
                identity=identity,
                controllable=reason is None,
                deny_reason=reason,
            )
        )
    return {
        "windows": [window.to_json() for window in windows],
        "displays": [monitor.to_json() for monitor in _enumerate_monitors()],
        "helper": {"pid": os.getpid(), "version": "0.1.0"},
        "total": len(identities),
        "truncated": len(identities) > len(selected),
    }

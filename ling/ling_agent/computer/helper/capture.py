"""Window screenshots for the computer-use helper."""

from __future__ import annotations

import ctypes
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab

from ...paths import default_state_root
from ..policy import observe_deny_reason, window_deny_reason
from ..types import ComputerError, WindowInfo
from . import win32

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 2
DIB_RGB_COLORS = 0
CAPTURE_RETENTION_SECONDS = 24 * 60 * 60
CAPTURE_LEASE_SECONDS = 300


@dataclass(slots=True)
class CaptureLease:
    """Screen mapping for one screenshot, valid for a short time."""

    hwnd: int
    left: int
    top: int
    width: int
    height: int
    original_width: int
    original_height: int
    scale: float
    created_at: float
    geometry: str = "window"
    pid: int = 0
    creation_time: int = 0


_leases: dict[str, CaptureLease] = {}
_leases_lock = threading.Lock()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


user32.GetWindowRect.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.RECT),
]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [
    wintypes.HWND,
    wintypes.HDC,
    wintypes.UINT,
]
user32.PrintWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int


def _parse_window_id(window_id: str) -> int:
    if not isinstance(window_id, str) or not window_id.startswith("w-"):
        raise ComputerError(
            "INVALID_ARGS",
            "window_id must look like w-<hwnd>.",
        )
    try:
        return int(window_id[2:])
    except ValueError as exc:
        raise ComputerError(
            "INVALID_ARGS",
            "window_id must look like w-<hwnd>.",
        ) from exc


def _print_window(hwnd: int) -> tuple[Image.Image, tuple[int, int]]:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ComputerError(
            "CAPTURE_FAILED",
            "Unable to read the window bounds.",
        )
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        raise ComputerError(
            "CAPTURE_FAILED",
            "The window has no drawable area.",
        )
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        raise ComputerError(
            "CAPTURE_FAILED",
            "Unable to get the window device context.",
        )
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not memory_dc or not bitmap:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
        raise ComputerError(
            "CAPTURE_FAILED",
            "Unable to allocate the capture bitmap.",
        )
    old = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(
            hwnd,
            memory_dc,
            PW_RENDERFULLCONTENT,
        ):
            raise ComputerError(
                "CAPTURE_FAILED",
                "PrintWindow failed for this window.",
            )
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            DIB_RGB_COLORS,
        )
        if lines != height:
            raise ComputerError(
                "CAPTURE_FAILED",
                "Unable to read the captured pixels.",
            )
        image = Image.frombuffer(
            "RGBA",
            (width, height),
            buffer,
            "raw",
            "BGRA",
            0,
            1,
        ).convert("RGB")
        if _is_black(image):
            raise ComputerError(
                "CAPTURE_BLACK",
                "The window returned a black capture.",
            )
        return image, (width, height), int(rect.left), int(rect.top)
    finally:
        if old:
            gdi32.SelectObject(memory_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def _is_black(image: Image.Image) -> bool:
    sample = image.resize((32, 32))
    extrema = sample.convert("L").getextrema()
    return extrema is not None and extrema[1] < 8


def _screen_window(identity: Any) -> tuple[Image.Image, tuple[int, int]]:
    bounds = identity.bounds
    if identity.minimized or not identity.visible:
        raise ComputerError(
            "WINDOW_HIDDEN",
            "The window is hidden or minimized; focus it first.",
            hint="Use computer_focus and retry the screenshot.",
        )
    bbox = (bounds.left, bounds.top, bounds.right, bounds.bottom)
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    return image.convert("RGB"), image.size


def _prune(directory: Path) -> None:
    cutoff = time.time() - CAPTURE_RETENTION_SECONDS
    try:
        entries = list(directory.glob("cap-*.png"))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass


def _remember_lease(
    capture_id: str,
    hwnd: int,
    left: int,
    top: int,
    width: int,
    height: int,
    original_width: int,
    original_height: int,
    scale: float,
    geometry: str,
    pid: int = 0,
    creation_time: int = 0,
) -> None:
    now = time.time()
    lease = CaptureLease(
        hwnd=int(hwnd),
        left=int(left),
        top=int(top),
        width=int(width),
        height=int(height),
        original_width=int(original_width),
        original_height=int(original_height),
        scale=float(scale),
        created_at=now,
        geometry=geometry,
        pid=int(pid),
        creation_time=int(creation_time),
    )
    with _leases_lock:
        _leases[capture_id] = lease
        stale = [
            key
            for key, item in _leases.items()
            if now - item.created_at > CAPTURE_LEASE_SECONDS
        ]
        for key in stale:
            _leases.pop(key, None)


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return None
    return (
        int(rect.left),
        int(rect.top),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


def resolve_capture_point(
    capture_id: str,
    x: int,
    y: int,
    hwnd: int,
) -> tuple[int, int]:
    """Map screenshot pixels to physical screen coordinates.

    The window must still have the same bounds as when the screenshot was
    taken; otherwise the image no longer describes the current layout.
    """

    if not isinstance(capture_id, str) or not capture_id:
        raise ComputerError(
            "INVALID_ARGS",
            "capture_id is required for coordinate actions.",
        )
    if (
        not isinstance(x, int)
        or isinstance(x, bool)
        or not isinstance(y, int)
        or isinstance(y, bool)
    ):
        raise ComputerError(
            "INVALID_ARGS",
            "x and y must be integer screenshot coordinates.",
        )
    now = time.time()
    with _leases_lock:
        lease = _leases.get(capture_id)
        if lease is not None and now - lease.created_at > CAPTURE_LEASE_SECONDS:
            _leases.pop(capture_id, None)
            lease = None
    if lease is None:
        raise ComputerError(
            "STALE_CAPTURE",
            "Screenshot is no longer available; take a new screenshot.",
        )
    if lease.hwnd != int(hwnd):
        raise ComputerError(
            "STALE_CAPTURE",
            "The screenshot belongs to a different window; take a new one.",
        )
    if not 0 <= x < lease.width or not 0 <= y < lease.height:
        raise ComputerError(
            "INVALID_ARGS",
            (
                "Screenshot coordinates are out of range: "
                f"{lease.width}x{lease.height}."
            ),
        )
    current = win32.describe_window(int(hwnd))
    if current is None:
        raise ComputerError(
            "WINDOW_NOT_FOUND",
            f"Window is gone: w-{hwnd}",
        )
    if (
        lease.pid > 0
        and current.pid != lease.pid
    ) or (
        lease.creation_time > 0
        and current.creation_time != lease.creation_time
    ):
        raise ComputerError(
            "STALE_CAPTURE",
            "The window was replaced by another process; take a new screenshot.",
        )
    if current.minimized:
        raise ComputerError(
            "WINDOW_MINIMIZED",
            "The window was minimized; restore it and take a new screenshot.",
        )
    if not current.visible:
        raise ComputerError(
            "WINDOW_HIDDEN",
            "The window is hidden; focus it and take a new screenshot.",
        )
    if lease.geometry == "dwm":
        current_left = int(current.bounds.left)
        current_top = int(current.bounds.top)
        current_width = int(current.bounds.right - current.bounds.left)
        current_height = int(current.bounds.bottom - current.bounds.top)
    else:
        rect = _window_rect(int(hwnd))
        if rect is None:
            raise ComputerError(
                "WINDOW_NOT_FOUND",
                f"Window is gone: w-{hwnd}",
            )
        current_left, current_top, current_width, current_height = rect
    if (
        abs(current_left - lease.left) > 1
        or abs(current_top - lease.top) > 1
        or abs(current_width - lease.original_width) > 1
        or abs(current_height - lease.original_height) > 1
    ):
        raise ComputerError(
            "STALE_CAPTURE",
            "The window moved or resized; take a new screenshot.",
        )
    factor_x = lease.original_width / max(1, lease.width)
    factor_y = lease.original_height / max(1, lease.height)
    screen_x = lease.left + round(x * factor_x)
    screen_y = lease.top + round(y * factor_y)
    screen_x = min(
        lease.left + lease.original_width - 1,
        max(lease.left, screen_x),
    )
    screen_y = min(
        lease.top + lease.original_height - 1,
        max(lease.top, screen_y),
    )
    return int(screen_x), int(screen_y)


def capture_window(
    *,
    window_id: str,
    max_width: int = 1280,
) -> dict[str, Any]:
    """Capture one window to a PNG and return its metadata."""

    if not isinstance(max_width, int) or isinstance(max_width, bool):
        raise ComputerError(
            "INVALID_ARGS",
            "max_width must be an integer.",
        )
    if not 320 <= max_width <= 3840:
        raise ComputerError(
            "INVALID_ARGS",
            "max_width must be between 320 and 3840.",
        )
    hwnd = _parse_window_id(window_id)
    identity = win32.describe_window(hwnd)
    if identity is None:
        raise ComputerError(
            "WINDOW_NOT_FOUND",
            f"Window is gone: {window_id}",
        )
    denial = window_deny_reason(identity)
    if denial is not None:
        raise ComputerError("APP_BLOCKED", denial)
    observe_denial = observe_deny_reason(identity)
    if observe_denial is not None:
        raise ComputerError("APP_BLOCKED", observe_denial)
    if identity.minimized:
        raise ComputerError(
            "WINDOW_MINIMIZED",
            "The window is minimized; focus it before capturing.",
            hint="Use computer_focus and retry the screenshot.",
        )
    method = "printwindow"
    geometry = "window"
    left = int(identity.bounds.left)
    top = int(identity.bounds.top)
    try:
        image, size, left, top = _print_window(hwnd)
    except ComputerError:
        if identity.visible:
            image, size = _screen_window(identity)
            method = "screen"
            geometry = "dwm"
        else:
            raise
    if method == "printwindow":
        bounds = identity.bounds
        offset_x = max(0, int(bounds.left) - left)
        offset_y = max(0, int(bounds.top) - top)
        crop_width = min(
            image.width - offset_x,
            int(bounds.right - bounds.left),
        )
        crop_height = min(
            image.height - offset_y,
            int(bounds.bottom - bounds.top),
        )
        if crop_width > 0 and crop_height > 0 and (
            offset_x
            or offset_y
            or crop_width != image.width
            or crop_height != image.height
        ):
            image = image.crop(
                (
                    offset_x,
                    offset_y,
                    offset_x + crop_width,
                    offset_y + crop_height,
                )
            )
            size = image.size
            left = int(bounds.left)
            top = int(bounds.top)
            geometry = "dwm"
    original_width, original_height = size
    scale = min(1.0, max_width / max(1, original_width))
    if scale < 1.0:
        image = image.resize(
            (
                max(1, round(original_width * scale)),
                max(1, round(original_height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    capture_id = f"cap-{uuid.uuid4().hex[:12]}"
    directory = default_state_root() / "computer" / "captures"
    directory.mkdir(parents=True, exist_ok=True)
    _prune(directory)
    path = directory / f"{capture_id}.png"
    image.save(path, "PNG", compress_level=6)
    _remember_lease(
        capture_id,
        int(identity.hwnd),
        left,
        top,
        image.width,
        image.height,
        original_width,
        original_height,
        scale,
        geometry,
        identity.pid,
        identity.creation_time,
    )
    denial = window_deny_reason(identity)
    window = WindowInfo(
        identity=identity,
        controllable=denial is None,
        deny_reason=denial,
    ).to_json()
    return {
        "captureId": capture_id,
        "path": str(path),
        "width": image.width,
        "height": image.height,
        "originalWidth": original_width,
        "originalHeight": original_height,
        "scale": round(scale, 4),
        "mimeType": "image/png",
        "method": method,
        "bytes": path.stat().st_size,
        "window": window,
    }


__all__ = [
    "CAPTURE_LEASE_SECONDS",
    "CaptureLease",
    "capture_window",
    "resolve_capture_point",
]

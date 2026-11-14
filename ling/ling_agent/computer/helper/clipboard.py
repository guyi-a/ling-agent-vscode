"""Clipboard paste with save/restore for the computer-use helper."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

from .input import press_key

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_TEXT = 1
CF_OEMTEXT = 7
CF_UNICODETEXT = 13
CF_LOCALE = 16
GMEM_MOVEABLE = 0x0002

_TEXT_FORMATS = frozenset({CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE})

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def _open_clipboard(retries: int = 6) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_text(text: str) -> bool:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _open_clipboard():
        return False
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        user32.CloseClipboard()
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        user32.CloseClipboard()
        return False
    try:
        ctypes.memmove(pointer, ctypes.addressof(data), size)
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.EmptyClipboard():
        kernel32.GlobalFree(handle)
        user32.CloseClipboard()
        return False
    if not user32.SetClipboardData(CF_UNICODETEXT, handle):
        kernel32.GlobalFree(handle)
        user32.CloseClipboard()
        return False
    user32.CloseClipboard()
    return True


def _clipboard_is_text_only() -> bool:
    if not _open_clipboard():
        return False
    try:
        formats: list[int] = []
        current = 0
        while True:
            current = int(user32.EnumClipboardFormats(current))
            if not current:
                break
            formats.append(current)
        return all(item in _TEXT_FORMATS for item in formats)
    finally:
        user32.CloseClipboard()


def _restore(saved: str | None) -> None:
    if saved is None:
        if _open_clipboard():
            try:
                user32.EmptyClipboard()
            finally:
                user32.CloseClipboard()
        return
    _set_text(saved)


def paste_text(text: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Paste text via Ctrl+V and report what happened to the clipboard.

    Text clipboard content is saved and restored. Non-text content is only
    replaced when ``overwrite`` is true; the result says so, because rich
    clipboard data cannot be restored by this helper.
    """

    if not isinstance(text, str) or not text:
        return {"ok": False, "reason": "empty"}
    text_only = _clipboard_is_text_only()
    if not text_only and not overwrite:
        return {"ok": False, "reason": "non_text"}
    saved = _clipboard_text() if text_only else None
    if not _set_text(text):
        return {"ok": False, "reason": "busy"}
    try:
        press_key("v", ["ctrl"])
    except OSError:
        _restore(saved)
        return {"ok": False, "reason": "input_failed"}
    time.sleep(0.3)
    _restore(saved)
    return {
        "ok": True,
        "clipboardRestored": saved is not None,
        "clipboardReplaced": not text_only,
    }


__all__ = ["paste_text"]

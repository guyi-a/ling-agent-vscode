"""Keyboard and mouse input for the computer-use helper."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = (
    ctypes.c_ulonglong
    if ctypes.sizeof(ctypes.c_void_p) == 8
    else ctypes.c_ulong
)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("value", _INPUTUNION),
    ]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

user32.SendInput.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(INPUT),
    ctypes.c_int,
]
user32.SendInput.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.BOOL,
]
user32.AttachThreadInput.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_MODIFIERS = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "meta": 0x5B,
}

_KEYS = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
}

_PUNCTUATION = {
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}

_KEY_NAMES = {
    "backslash": 0xDC,
    "bracketleft": 0xDB,
    "bracketright": 0xDD,
    "comma": 0xBC,
    "equals": 0xBB,
    "grave": 0xC0,
    "minus": 0xBD,
    "period": 0xBE,
    "quote": 0xDE,
    "semicolon": 0xBA,
    "slash": 0xBF,
}

_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

class InputLedger:
    """Track keys and mouse buttons held by the helper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: set[int] = set()
        self._buttons: set[str] = set()

    def mark_down(
        self,
        *,
        keys: tuple[int, ...] = (),
        buttons: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            self._keys.update(keys)
            self._buttons.update(buttons)

    def mark_up(
        self,
        *,
        keys: tuple[int, ...] = (),
        buttons: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            self._keys.difference_update(keys)
            self._buttons.difference_update(buttons)

    def pending_keys(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._keys))

    def pending_buttons(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._buttons)

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()
            self._buttons.clear()


ledger = InputLedger()

_send_lock = threading.Lock()


def _key_event(vk: int, scan: int, flags: int) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.value.ki = KEYBDINPUT(
        wVk=vk,
        wScan=scan,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return event


def _mouse_event(
    dx: int,
    dy: int,
    data: int,
    flags: int,
) -> INPUT:
    event = INPUT()
    event.type = INPUT_MOUSE
    event.value.mi = MOUSEINPUT(
        dx=dx,
        dy=dy,
        mouseData=data & 0xFFFFFFFF,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return event


def _send(events: list[INPUT]) -> None:
    if not events:
        return
    with _send_lock:
        buffer = (INPUT * len(events))(*events)
        sent = user32.SendInput(
            len(events),
            buffer,
            ctypes.sizeof(INPUT),
        )
        error = ctypes.get_last_error()
    if sent != len(events):
        raise OSError(
            error,
            "SendInput did not send all input events",
        )


def _resolve_modifier(name: str) -> int:
    value = _MODIFIERS.get(name.strip().lower())
    if value is None:
        raise ValueError(f"Unsupported modifier: {name}")
    return value


def _resolve_key(name: str) -> int:
    normalized = name.strip().lower()
    if normalized in _KEYS:
        return _KEYS[normalized]
    if normalized in _KEY_NAMES:
        return _KEY_NAMES[normalized]
    if len(normalized) == 1:
        upper = normalized.upper()
        if upper.isalnum():
            return ord(upper)
        if normalized in _PUNCTUATION:
            return _PUNCTUATION[normalized]
    if normalized.startswith("f") and normalized[1:].isdigit():
        number = int(normalized[1:])
        if 1 <= number <= 12:
            return 0x70 + number - 1
    raise ValueError(f"Unsupported key: {name}")


def normalize_key_chord(
    key: str,
    modifiers: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Normalize ``ctrl+s`` shorthand into key plus modifiers."""

    if not isinstance(key, str) or not key.strip():
        raise ValueError("key must be a non-empty string")
    normalized = key.strip()
    shorthand = [
        part.strip()
        for part in normalized.split("+")
        if part.strip()
    ]
    if len(shorthand) > 1:
        normalized = shorthand[-1]
        modifiers = [
            *(modifiers or []),
            *shorthand[:-1],
        ]
    return normalized, list(modifiers or [])


def validate_key_chord(
    key: str,
    modifiers: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Validate a key chord without sending any input."""

    normalized, normalized_modifiers = normalize_key_chord(
        key,
        modifiers,
    )
    for modifier in normalized_modifiers:
        _resolve_modifier(modifier)
    _resolve_key(normalized)
    return normalized, normalized_modifiers


def press_key(key: str, modifiers: list[str] | None = None) -> None:
    """Send one key combination through SendInput."""

    key, modifiers = normalize_key_chord(key, modifiers)
    modifier_vks = [
        _resolve_modifier(modifier) for modifier in (modifiers or [])
    ]
    key_vk = _resolve_key(key)
    events = [_key_event(vk, 0, 0) for vk in modifier_vks]
    events.append(_key_event(key_vk, 0, 0))
    events.append(_key_event(key_vk, 0, KEYEVENTF_KEYUP))
    events.extend(
        _key_event(vk, 0, KEYEVENTF_KEYUP)
        for vk in reversed(modifier_vks)
    )
    try:
        _send(events)
    except OSError:
        ledger.clear()
        raise


def _utf16_units(text: str) -> list[int]:
    encoded = text.encode("utf-16-le")
    return [
        int.from_bytes(encoded[index : index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]


def type_text(text: str) -> int:
    """Type text with KEYEVENTF_UNICODE; returns the number of characters."""

    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")
    pending: list[INPUT] = []
    sent = 0
    for char in text:
        if char == "\r":
            continue
        if char in ("\n", "\t"):
            if pending:
                _send(pending)
                pending = []
                time.sleep(0.005)
            press_key("enter" if char == "\n" else "tab")
            sent += 1
            continue
        for unit in _utf16_units(char):
            pending.append(
                _key_event(0, unit, KEYEVENTF_UNICODE)
            )
            pending.append(
                _key_event(
                    0,
                    unit,
                    KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                )
            )
            sent += 1
            if len(pending) >= 64:
                _send(pending)
                pending = []
                time.sleep(0.005)
    if pending:
        _send(pending)
    return sent


def _normalize_axis(value: int, origin: int, extent: int) -> int:
    if extent <= 1:
        return 0
    normalized = round((value - origin) * 65535 / (extent - 1))
    return max(0, min(65535, int(normalized)))


def _virtual_screen() -> tuple[int, int, int, int]:
    return (
        int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    )


def _move_to(x: int, y: int) -> None:
    left, top, width, height = _virtual_screen()
    _send(
        [
            _mouse_event(
                _normalize_axis(x, left, width),
                _normalize_axis(y, top, height),
                0,
                MOUSEEVENTF_MOVE
                | MOUSEEVENTF_ABSOLUTE
                | MOUSEEVENTF_VIRTUALDESK,
            )
        ]
    )


def click_at(
    x: int,
    y: int,
    *,
    button: str = "left",
    clicks: int = 1,
) -> None:
    """Click at physical screen coordinates."""

    flags = _BUTTONS.get(button)
    if flags is None:
        raise ValueError(f"Unsupported mouse button: {button}")
    if not 1 <= clicks <= 3:
        raise ValueError("clicks must be between 1 and 3")
    down_flag, up_flag = flags
    _move_to(x, y)
    time.sleep(0.02)
    for _ in range(clicks):
        _send([_mouse_event(0, 0, 0, down_flag)])
        ledger.mark_down(buttons=(button,))
        _send([_mouse_event(0, 0, 0, up_flag)])
        ledger.mark_up(buttons=(button,))
        time.sleep(0.03)


def scroll_at(
    x: int,
    y: int,
    direction: str,
    *,
    amount: int = 3,
) -> None:
    """Scroll with the wheel at physical screen coordinates."""

    if not 1 <= amount <= 10:
        raise ValueError("amount must be between 1 and 10")
    if direction not in ("up", "down", "left", "right"):
        raise ValueError(f"Unsupported scroll direction: {direction}")
    impulse = WHEEL_DELTA * amount
    if direction == "up":
        flag, delta = MOUSEEVENTF_WHEEL, impulse
    elif direction == "down":
        flag, delta = MOUSEEVENTF_WHEEL, -impulse
    elif direction == "right":
        flag, delta = MOUSEEVENTF_HWHEEL, impulse
    else:
        flag, delta = MOUSEEVENTF_HWHEEL, -impulse
    _move_to(x, y)
    time.sleep(0.02)
    _send([_mouse_event(0, 0, delta, flag)])


def drag(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    *,
    duration_ms: int = 400,
) -> None:
    """Drag with the left mouse button between physical screen points."""

    duration_ms = max(120, min(2000, duration_ms))
    steps = max(4, min(30, duration_ms // 40))
    _move_to(from_x, from_y)
    time.sleep(0.05)
    _send([_mouse_event(0, 0, 0, MOUSEEVENTF_LEFTDOWN)])
    ledger.mark_down(buttons=("left",))
    try:
        for index in range(1, steps + 1):
            x = round(from_x + (to_x - from_x) * index / steps)
            y = round(from_y + (to_y - from_y) * index / steps)
            _move_to(x, y)
            time.sleep(duration_ms / steps / 1000)
    finally:
        _send([_mouse_event(0, 0, 0, MOUSEEVENTF_LEFTUP)])
        ledger.mark_up(buttons=("left",))


def ensure_foreground(hwnd: int) -> bool:
    """Bring a window to the foreground for keyboard or mouse fallback input."""

    target = int(hwnd)
    if int(user32.GetForegroundWindow() or 0) == target:
        return True
    foreground = int(user32.GetForegroundWindow() or 0)
    attached = False
    if foreground:
        foreground_thread = int(
            user32.GetWindowThreadProcessId(foreground, None)
        )
        current_thread = int(kernel32.GetCurrentThreadId())
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(
                user32.AttachThreadInput(
                    current_thread,
                    foreground_thread,
                    True,
                )
            )
    try:
        user32.SetForegroundWindow(target)
        user32.BringWindowToTop(target)
    finally:
        if attached:
            user32.AttachThreadInput(
                current_thread,
                foreground_thread,
                False,
            )
    time.sleep(0.1)
    return int(user32.GetForegroundWindow() or 0) == target


def release_all() -> None:
    """Release every key and mouse button the helper is holding."""

    keys = ledger.pending_keys()
    buttons = ledger.pending_buttons()
    events = [_key_event(vk, 0, KEYEVENTF_KEYUP) for vk in keys]
    for button in buttons:
        flags = _BUTTONS.get(button)
        if flags is not None:
            events.append(_mouse_event(0, 0, 0, flags[1]))
    try:
        _send(events)
    finally:
        ledger.clear()


__all__ = [
    "InputLedger",
    "click_at",
    "drag",
    "ensure_foreground",
    "ledger",
    "normalize_key_chord",
    "press_key",
    "release_all",
    "scroll_at",
    "type_text",
    "validate_key_chord",
]

"""Launch a desktop application and wait for its window."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from ..policy import (
    hard_deny_reason,
    observe_deny_reason,
    window_deny_reason,
)
from ..types import ComputerError
from . import win32

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400
SEE_MASK_NOASYNC = 0x00000100
SW_SHOWNORMAL = 1
SW_SHOW = 5
SW_RESTORE = 9

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
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

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_ELEVATION_REQUIRED = 740
ERROR_CANCELLED = 1223

_MAX_ARGUMENTS = 32
_MAX_ARGUMENT_LENGTH = 2048
_MAX_WAIT_MS = 15_000
_POLL_SECONDS = 0.1


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


shell32.ShellExecuteExW.argtypes = [
    ctypes.POINTER(SHELLEXECUTEINFOW)
]
shell32.ShellExecuteExW.restype = wintypes.BOOL
kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
kernel32.GetProcessId.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def _ensure_executable(path: Path) -> Path:
    if not path.is_file():
        raise ComputerError(
            "APP_NOT_FOUND",
            f"Executable not found: {path}",
        )
    if path.suffix.lower() != ".exe":
        raise ComputerError(
            "APP_LAUNCH_FAILED",
            f"Only .exe files can be launched: {path}",
            hint=(
                "Pass an installed executable name such as notepad.exe, "
                "not a shell script or shim."
            ),
        )
    return path


def _app_paths(name: str) -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    key_path = (
        "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
        + name
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as handle:
                value, _kind = winreg.QueryValueEx(handle, "")
        except OSError:
            continue
        candidate = Path(str(value).strip().strip('"'))
        if candidate.is_file():
            return candidate
    return None


def resolve_executable(app: str) -> Path:
    """Resolve a bare executable name or path, avoiding shell shims."""

    raw = (app or "").strip().strip('"')
    if not raw:
        raise ComputerError(
            "INVALID_ARGS",
            "computer_launch requires an app name or path.",
        )
    direct = Path(raw)
    if ("\\" in raw or "/" in raw) and direct.is_file():
        return _ensure_executable(direct)
    name = raw if raw.lower().endswith(".exe") else f"{raw}.exe"
    system_root = Path(
        os.environ.get("SystemRoot", r"C:\Windows")
    )
    for base in (
        system_root / "System32",
        system_root / "SysWOW64",
        system_root,
    ):
        candidate = base / name
        if candidate.is_file():
            return _ensure_executable(candidate)
    app_path = _app_paths(name)
    if app_path is not None:
        return _ensure_executable(app_path)
    running = win32.list_windows(
        include_all=True,
        query="",
        max_windows=400,
    )["windows"]
    for window in running:
        if str(window.get("exeName", "")).lower() != name.lower():
            continue
        path = str(window.get("exePath", ""))
        candidate = Path(path)
        if path and candidate.is_file():
            return _ensure_executable(candidate)
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        alias = (
            Path(local_appdata)
            / "Microsoft"
            / "WindowsApps"
            / name
        )
        if alias.is_file():
            return _ensure_executable(alias)
    found = shutil.which(name)
    if found:
        return _ensure_executable(Path(found))
    raise ComputerError(
        "APP_NOT_FOUND",
        f"Unable to resolve an executable named {name}.",
        hint="Use the full path to the .exe, or install the app.",
    )


def _validate_arguments(
    arguments: list[str] | None,
) -> list[str]:
    if arguments is None:
        return []
    if not isinstance(arguments, list):
        raise ComputerError(
            "INVALID_ARGS",
            "arguments must be a list of strings.",
        )
    if len(arguments) > _MAX_ARGUMENTS:
        raise ComputerError(
            "INVALID_ARGS",
            f"At most {_MAX_ARGUMENTS} arguments are allowed.",
        )
    result: list[str] = []
    for argument in arguments:
        if not isinstance(argument, str):
            raise ComputerError(
                "INVALID_ARGS",
                "arguments must be a list of strings.",
            )
        if len(argument) > _MAX_ARGUMENT_LENGTH or "\x00" in argument:
            raise ComputerError(
                "INVALID_ARGS",
                "An argument is too long or contains a null byte.",
            )
        result.append(argument)
    return result


def _launch_error(
    error: int,
    exe: Path,
) -> ComputerError:
    if error in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
        return ComputerError(
            "APP_NOT_FOUND",
            f"The executable could not be started: {exe}",
        )
    if error == ERROR_ACCESS_DENIED:
        return ComputerError(
            "APP_BLOCKED",
            f"Access denied while launching {exe.name}.",
        )
    if error == ERROR_ELEVATION_REQUIRED:
        return ComputerError(
            "APP_LAUNCH_ELEVATION",
            f"{exe.name} requires elevation.",
            hint="Ask the user to start it as administrator.",
        )
    if error == ERROR_CANCELLED:
        return ComputerError(
            "APP_LAUNCH_CANCELLED",
            "The launch was cancelled.",
        )
    return ComputerError(
        "APP_LAUNCH_FAILED",
        f"ShellExecute failed for {exe.name} (error {error}).",
    )


_HELPER_TITLE_TOKENS = (
    "ime",
    "tsf",
    "sogou",
    "hint",
    "trayicon",
    "powermessagewindow",
    "systemmessagewindow",
)


def _is_helper_window(window: dict[str, Any]) -> bool:
    bounds = window.get("bounds")
    if isinstance(bounds, dict) and not window.get("minimized"):
        width = int(bounds.get("width", 0) or 0)
        height = int(bounds.get("height", 0) or 0)
        if width < 80 or height < 80:
            return True
    label = (
        f"{window.get('title', '')} "
        f"{window.get('className', '')}"
    ).lower()
    return any(token in label for token in _HELPER_TITLE_TOKENS)


def _normalized_path(value: str) -> str:
    if not value:
        return ""
    try:
        return os.path.normcase(os.path.abspath(value))
    except OSError:
        return os.path.normcase(value)


def _pick_existing_window(
    windows: list[dict[str, Any]],
    exe_path: str,
    exe_name: str,
) -> dict[str, Any] | None:
    """Pick the best existing window for an already-running app."""

    normalized = _normalized_path(exe_path)
    candidates = [
        window
        for window in windows
        if not str(window.get("className", "")).startswith(
            "LingControlBorder"
        )
        and not window.get("toolWindow")
        and not _is_helper_window(window)
    ]
    exact = [
        window
        for window in candidates
        if normalized
        and _normalized_path(str(window.get("exePath", "")))
        == normalized
    ]
    matches = exact or [
        window
        for window in candidates
        if str(window.get("exeName", "")).lower() == exe_name
    ]
    if not matches:
        return None

    def created_at(window: dict[str, Any]) -> int:
        try:
            return int(window.get("processCreationTime", 0) or 0)
        except (TypeError, ValueError):
            return 0

    # The oldest process is the primary instance. A duplicate launch (for
    # example a second WeChat instance) is newer and must not win just
    # because its login window is visible.
    matches.sort(
        key=lambda window: (
            0 if created_at(window) > 0 else 1,
            created_at(window),
            0
            if window.get("visible") and not window.get("minimized")
            else 1,
            0 if window.get("visible") else 1,
            0 if not window.get("minimized") else 1,
            0 if window.get("title") else 1,
            str(window.get("title", "")).casefold(),
        )
    )
    return matches[0]


def _refresh_window(window_id: str) -> dict[str, Any] | None:
    payload = win32.list_windows(
        include_all=True,
        query="",
        max_windows=400,
    )
    for window in payload["windows"]:
        if window["id"] == window_id:
            return window
    return None


def _bring_to_front(hwnd: int, *, minimized: bool) -> bool:
    target = int(hwnd)
    if int(user32.GetForegroundWindow() or 0) == target:
        return True
    user32.ShowWindow(target, SW_RESTORE if minimized else SW_SHOW)
    foreground = int(user32.GetForegroundWindow() or 0)
    foreground_thread = 0
    current_thread = int(kernel32.GetCurrentThreadId())
    attached = False
    if foreground:
        foreground_thread = int(
            user32.GetWindowThreadProcessId(foreground, None)
        )
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


def _restore_window(window: dict[str, Any]) -> dict[str, Any]:
    window_id = str(window.get("id", ""))
    if window_id.startswith("w-"):
        hwnd = int(window_id[2:])
        _bring_to_front(hwnd, minimized=bool(window.get("minimized")))
        refreshed = _refresh_window(window_id)
        if refreshed is not None:
            return refreshed
    return window


def focus_window(window_id: str) -> dict[str, Any]:
    """Restore and foreground one existing top-level window."""

    if not isinstance(window_id, str) or not window_id.startswith("w-"):
        raise ComputerError(
            "INVALID_ARGS",
            "window_id must look like w-<hwnd>.",
        )
    try:
        hwnd = int(window_id[2:])
    except ValueError as exc:
        raise ComputerError(
            "INVALID_ARGS",
            "window_id must look like w-<hwnd>.",
        ) from exc
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
    foreground = _bring_to_front(
        hwnd,
        minimized=identity.minimized,
    )
    window = _refresh_window(window_id)
    restored = bool(
        window is not None
        and window.get("visible")
        and not window.get("minimized")
    )
    return {
        "window": window,
        "foreground": foreground,
        "restored": restored,
    }


def _window_candidates(
    before_ids: set[str],
    exe_name: str,
    pid: int,
) -> list[dict[str, Any]]:
    payload = win32.list_windows(
        include_all=False,
        query="",
        max_windows=200,
    )
    windows = [
        window
        for window in payload["windows"]
        if window["id"] not in before_ids
        and not window.get("minimized")
        and window.get("visible", True)
        and (
            (pid > 0 and int(window.get("pid", 0)) == pid)
            or str(window.get("exeName", "")).lower()
            == exe_name
        )
    ]
    if not windows:
        return []
    windows.sort(
        key=lambda window: (
            0 if int(window.get("pid", 0)) == pid else 1,
            0 if window.get("title") else 1,
            str(window.get("title", "")).casefold(),
        )
    )
    return windows


def _existing_window(
    exe_name: str,
) -> dict[str, Any] | None:
    payload = win32.list_windows(
        include_all=False,
        query="",
        max_windows=200,
    )
    matches = [
        window
        for window in payload["windows"]
        if str(window.get("exeName", "")).lower() == exe_name
        and not window.get("minimized")
        and window.get("title")
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def launch_app(
    *,
    app: str,
    arguments: list[str] | None = None,
    wait_for_window_ms: int = 5000,
    should_abort: Any = None,
) -> dict[str, Any]:
    """Launch one application and wait for a new or reused window."""

    if (
        not isinstance(wait_for_window_ms, int)
        or isinstance(wait_for_window_ms, bool)
        or not 0 <= wait_for_window_ms <= _MAX_WAIT_MS
    ):
        raise ComputerError(
            "INVALID_ARGS",
            f"wait_for_window_ms must be 0..{_MAX_WAIT_MS}.",
        )
    exe = resolve_executable(app)
    denial = hard_deny_reason(exe.name)
    if denial is not None:
        raise ComputerError("APP_BLOCKED", denial)
    args = _validate_arguments(arguments)
    if not args:
        existing_payload = win32.list_windows(
            include_all=True,
            query="",
            max_windows=400,
        )
        existing = _pick_existing_window(
            existing_payload["windows"],
            str(exe),
            exe.name.lower(),
        )
        if existing is not None:
            restored = _restore_window(existing)
            result: dict[str, Any] = {
                "pid": int(restored.get("pid", 0)),
                "exe": str(exe),
                "arguments": [],
                "window": restored,
                "reused": True,
                "restored": bool(
                    restored.get("visible")
                    and not restored.get("minimized")
                ),
                "timedOut": False,
                "exitCode": None,
            }
            if not result["restored"]:
                result["hint"] = (
                    "The app is already running but its window is hidden "
                    "to the tray. Ask the user to open it from the tray; "
                    "do not launch another instance."
                )
            return result
        running = win32.find_running_processes(str(exe))
        if running:
            return {
                "pid": running[0],
                "exe": str(exe),
                "arguments": [],
                "window": None,
                "reused": True,
                "restored": False,
                "timedOut": False,
                "exitCode": None,
                "hint": (
                    "The app is already running but has no top-level "
                    "window. Ask the user to open it from the tray; do "
                    "not launch another instance."
                ),
            }
    before_ids = {
        window["id"]
        for window in win32.list_windows(
            include_all=False,
            query="",
            max_windows=200,
        )["windows"]
    }
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = (
        SEE_MASK_NOCLOSEPROCESS
        | SEE_MASK_FLAG_NO_UI
        | SEE_MASK_NOASYNC
    )
    info.lpVerb = "open"
    info.lpFile = str(exe)
    info.lpParameters = (
        subprocess.list2cmdline(args) if args else None
    )
    info.nShow = SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = int(ctypes.get_last_error())
        raise _launch_error(error, exe)
    pid = 0
    exit_code: int | None = None
    if info.hProcess:
        pid = int(kernel32.GetProcessId(info.hProcess))
    deadline = time.monotonic() + wait_for_window_ms / 1000
    window: dict[str, Any] | None = None
    reused = False
    try:
        while True:
            if should_abort is not None and should_abort():
                raise ComputerError(
                    "CONTROL_NOT_ACTIVE",
                    "Computer control was stopped during launch.",
                    may_have_taken_effect=True,
                )
            candidates = _window_candidates(
                before_ids,
                exe.name.lower(),
                pid,
            )
            if candidates:
                window = candidates[0]
                break
            if info.hProcess and exit_code is None:
                if kernel32.WaitForSingleObject(info.hProcess, 0) == 0:
                    code = wintypes.DWORD(0)
                    if kernel32.GetExitCodeProcess(
                        info.hProcess,
                        ctypes.byref(code),
                    ):
                        exit_code = int(code.value)
            if wait_for_window_ms <= 0:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(_POLL_SECONDS)
        if window is None:
            existing = _existing_window(exe.name.lower())
            if existing is not None:
                window = existing
                reused = True
    finally:
        if info.hProcess:
            kernel32.CloseHandle(info.hProcess)
    return {
        "pid": pid,
        "exe": str(exe),
        "arguments": args,
        "window": window,
        "reused": reused,
        "timedOut": window is None,
        "exitCode": exit_code,
    }


__all__ = ["focus_window", "launch_app", "resolve_executable"]

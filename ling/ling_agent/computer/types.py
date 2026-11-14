"""Shared types for Windows computer use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMPUTER_PROTOCOL_VERSION = 1


@dataclass(slots=True, frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def to_json(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
        }


@dataclass(slots=True, frozen=True)
class MonitorInfo:
    index: int
    primary: bool
    device: str
    bounds: Rect
    work_area: Rect
    dpi: int = 96

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "primary": self.primary,
            "device": self.device,
            "bounds": self.bounds.to_json(),
            "workArea": self.work_area.to_json(),
            "dpi": self.dpi,
        }


@dataclass(slots=True, frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    creation_time: int
    exe_path: str
    exe_name: str
    title: str
    class_name: str
    bounds: Rect
    visible: bool
    cloaked: bool
    minimized: bool
    aumid: str | None = None
    integrity: str = "unknown"
    tool_window: bool = False


@dataclass(slots=True, frozen=True)
class WindowInfo:
    identity: WindowIdentity
    controllable: bool
    deny_reason: str | None = None

    @property
    def window_id(self) -> str:
        return f"w-{self.identity.hwnd}"

    def to_json(self) -> dict[str, Any]:
        window = self.identity
        return {
            "id": self.window_id,
            "title": window.title,
            "app": window.exe_name or window.class_name,
            "exePath": window.exe_path,
            "exeName": window.exe_name,
            "pid": window.pid,
            "processCreationTime": window.creation_time,
            "className": window.class_name,
            "bounds": window.bounds.to_json(),
            "visible": window.visible,
            "minimized": window.minimized,
            "integrity": window.integrity,
            "aumid": window.aumid,
            "toolWindow": window.tool_window,
            "controllable": self.controllable,
            "denyReason": self.deny_reason,
        }


class ComputerError(Exception):
    """Structured computer-use error returned to the sidecar/model."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        may_have_taken_effect: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.may_have_taken_effect = may_have_taken_effect

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "mayHaveTakenEffect": self.may_have_taken_effect,
        }

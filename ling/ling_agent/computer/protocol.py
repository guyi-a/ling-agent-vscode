"""Helper JSON-RPC method names for computer use."""

from .types import COMPUTER_PROTOCOL_VERSION

INITIALIZE = "initialize"
PING = "ping"
SHUTDOWN = "shutdown"
WINDOWS_LIST = "windows.list"
WINDOW_OBSERVE = "window.observe"
UIA_SNAPSHOT = "uia.snapshot"
UIA_FIND = "uia.find"
INPUT_ACT = "input.act"
APP_LAUNCH = "app.launch"
WINDOW_FOCUS = "window.focus"
CONTROL_BEGIN = "control.begin"
CONTROL_END = "control.end"
CONTROL_STATUS = "control.status"
CAPTURE_WINDOW = "capture.window"

NOTIFY_READY = "helper.ready"
NOTIFY_STOP_REQUESTED = "control.stopRequested"
NOTIFY_IDLE_EXPIRED = "control.idleExpired"

__all__ = [
    "APP_LAUNCH",
    "CAPTURE_WINDOW",
    "COMPUTER_PROTOCOL_VERSION",
    "CONTROL_BEGIN",
    "CONTROL_END",
    "CONTROL_STATUS",
    "INITIALIZE",
    "INPUT_ACT",
    "NOTIFY_IDLE_EXPIRED",
    "NOTIFY_READY",
    "NOTIFY_STOP_REQUESTED",
    "PING",
    "SHUTDOWN",
    "UIA_FIND",
    "UIA_SNAPSHOT",
    "WINDOW_FOCUS",
    "WINDOW_OBSERVE",
    "WINDOWS_LIST",
]

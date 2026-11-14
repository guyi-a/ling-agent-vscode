"""Windows computer-use support for Ling."""

from .service import ComputerService
from .types import (
    COMPUTER_PROTOCOL_VERSION,
    ComputerError,
    MonitorInfo,
    Rect,
    WindowIdentity,
    WindowInfo,
)

__all__ = [
    "COMPUTER_PROTOCOL_VERSION",
    "ComputerError",
    "ComputerService",
    "MonitorInfo",
    "Rect",
    "WindowIdentity",
    "WindowInfo",
]

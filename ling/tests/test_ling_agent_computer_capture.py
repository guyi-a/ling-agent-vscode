"""Tests for screenshot coordinate leases."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from ling_agent.computer.helper import capture
from ling_agent.computer.types import ComputerError


def _identity(
    *,
    hwnd: int = 1234,
    pid: int = 100,
    creation_time: int = 1,
    left: int = 100,
    top: int = 50,
    right: int = 900,
    bottom: int = 650,
) -> SimpleNamespace:
    return SimpleNamespace(
        hwnd=hwnd,
        pid=pid,
        creation_time=creation_time,
        minimized=False,
        visible=True,
        bounds=SimpleNamespace(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        ),
    )


def _lease(**overrides: Any) -> capture.CaptureLease:
    values: dict[str, Any] = {
        "hwnd": 1234,
        "left": 100,
        "top": 50,
        "width": 800,
        "height": 600,
        "original_width": 1600,
        "original_height": 1200,
        "scale": 0.5,
        "created_at": time.time(),
        "geometry": "dwm",
    }
    values.update(overrides)
    return capture.CaptureLease(**values)


def test_resolve_capture_point_scales_to_screen() -> None:
    original = capture.win32.describe_window
    capture._leases.clear()
    capture._leases["cap-1"] = _lease()
    capture.win32.describe_window = lambda hwnd: _identity(
        right=1700,
        bottom=1250,
    )
    try:
        point = capture.resolve_capture_point("cap-1", 10, 20, 1234)
    finally:
        capture.win32.describe_window = original
        capture._leases.clear()
    assert point == (120, 90)


def test_resolve_capture_point_uses_window_rect_geometry() -> None:
    original = capture.win32.describe_window
    original_rect = capture._window_rect
    capture._leases.clear()
    capture._leases["cap-1"] = _lease(
        geometry="window",
        left=312,
        top=244,
        width=668,
        height=715,
        original_width=668,
        original_height=715,
        scale=1.0,
    )
    capture.win32.describe_window = lambda hwnd: _identity(
        left=319,
        top=248,
        right=973,
        bottom=956,
    )
    capture._window_rect = lambda hwnd: (312, 244, 668, 715)
    try:
        point = capture.resolve_capture_point("cap-1", 4, 5, 1234)
    finally:
        capture.win32.describe_window = original
        capture._window_rect = original_rect
        capture._leases.clear()
    assert point == (316, 249)


def test_resolve_capture_point_rejects_moved_window() -> None:
    original = capture.win32.describe_window
    capture._leases.clear()
    capture._leases["cap-1"] = _lease()
    capture.win32.describe_window = lambda hwnd: _identity(left=200)
    try:
        try:
            capture.resolve_capture_point("cap-1", 10, 20, 1234)
        except ComputerError as exc:
            assert exc.code == "STALE_CAPTURE"
        else:
            raise AssertionError("expected STALE_CAPTURE")
    finally:
        capture.win32.describe_window = original
        capture._leases.clear()


def test_resolve_capture_point_rejects_expired_capture() -> None:
    original = capture.win32.describe_window
    capture._leases.clear()
    capture._leases["cap-1"] = _lease(
        created_at=time.time() - capture.CAPTURE_LEASE_SECONDS - 1,
    )
    capture.win32.describe_window = lambda hwnd: _identity()
    try:
        try:
            capture.resolve_capture_point("cap-1", 10, 20, 1234)
        except ComputerError as exc:
            assert exc.code == "STALE_CAPTURE"
        else:
            raise AssertionError("expected STALE_CAPTURE")
    finally:
        capture.win32.describe_window = original
        capture._leases.clear()


def test_resolve_capture_point_rejects_out_of_range() -> None:
    original = capture.win32.describe_window
    capture._leases.clear()
    capture._leases["cap-1"] = _lease()
    capture.win32.describe_window = lambda hwnd: _identity()
    try:
        try:
            capture.resolve_capture_point("cap-1", 800, 20, 1234)
        except ComputerError as exc:
            assert exc.code == "INVALID_ARGS"
        else:
            raise AssertionError("expected INVALID_ARGS")
    finally:
        capture.win32.describe_window = original
        capture._leases.clear()


def test_resolve_capture_point_rejects_reused_handle() -> None:
    original = capture.win32.describe_window
    capture._leases.clear()
    capture._leases["cap-1"] = _lease(
        pid=100,
        creation_time=1,
    )
    capture.win32.describe_window = lambda hwnd: _identity(
        pid=200,
        creation_time=2,
    )
    try:
        try:
            capture.resolve_capture_point("cap-1", 10, 20, 1234)
        except ComputerError as exc:
            assert exc.code == "STALE_CAPTURE"
            assert "replaced" in exc.message
        else:
            raise AssertionError("expected STALE_CAPTURE")
    finally:
        capture.win32.describe_window = original
        capture._leases.clear()


if __name__ == "__main__":
    test_resolve_capture_point_scales_to_screen()
    test_resolve_capture_point_uses_window_rect_geometry()
    test_resolve_capture_point_rejects_moved_window()
    test_resolve_capture_point_rejects_expired_capture()
    test_resolve_capture_point_rejects_out_of_range()
    test_resolve_capture_point_rejects_reused_handle()
    print("computer capture tests passed")

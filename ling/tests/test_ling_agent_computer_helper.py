"""Tests for the Windows computer-use helper skeleton."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_list_windows_shape() -> None:
    if sys.platform != "win32":
        return
    from ling_agent.computer.helper import win32

    payload = win32.list_windows(include_all=True, query="", max_windows=20)
    assert payload["helper"]["pid"] > 0
    assert isinstance(payload["windows"], list)
    assert isinstance(payload["displays"], list)
    for window in payload["windows"]:
        assert isinstance(window["id"], str)
        assert isinstance(window["pid"], int)
        assert isinstance(window["bounds"], dict)
        assert "controllable" in window
        assert window["integrity"] in (
            "untrusted",
            "low",
            "medium",
            "high",
            "system",
            "unknown",
        )
        assert window["aumid"] is None or isinstance(
            window["aumid"], str
        )


def test_self_test_subprocess() -> None:
    if sys.platform != "win32":
        return
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ling_agent.computer.helper",
            "--self-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert isinstance(payload["windows"], list)
    assert isinstance(payload["displays"], list)


def test_real_uia_snapshot_opt_in() -> None:
    if (
        sys.platform != "win32"
        or os.environ.get("LING_COMPUTER_UIA_TEST") != "1"
    ):
        return
    from ling_agent.computer.helper.uia import snapshot_window
    from ling_agent.computer.helper.win32 import list_windows

    windows = list_windows(include_all=False, max_windows=50)["windows"]
    target = next(
        (
            window
            for window in windows
            if not window["minimized"] and window["controllable"]
        ),
        None,
    )
    if target is None:
        return
    result = snapshot_window(
        window_id=target["id"],
        max_depth=6,
        max_nodes=100,
        max_chars=12_000,
        query="",
    )
    assert result["snapshotId"].startswith("snap-")
    assert isinstance(result["tree"], str)
    assert result["nodeCount"] >= 0


def test_helper_client_allows_concurrent_requests() -> None:
    import asyncio

    from ling_agent.computer.helper_client import HelperClient

    client = HelperClient()
    gate = asyncio.Event()
    started = asyncio.Event()

    async def ensure() -> None:
        return None

    async def request_locked(
        method: str,
        params: dict,
        *,
        timeout: float | None,
    ) -> dict:
        if method == "slow":
            started.set()
            await gate.wait()
            return {"slow": True}
        return {"fast": True}

    client._ensure_started_locked = ensure  # type: ignore[method-assign]
    client._request_locked = request_locked  # type: ignore[method-assign]

    async def scenario() -> None:
        slow = asyncio.create_task(client.request("slow"))
        await started.wait()
        fast = await asyncio.wait_for(
            client.request("fast"),
            timeout=1.0,
        )
        assert fast == {"fast": True}
        gate.set()
        assert await slow == {"slow": True}

    asyncio.run(scenario())


def test_overlay_strips_negative_origin_and_dpi() -> None:
    if sys.platform != "win32":
        return
    from ling_agent.computer.helper.overlay import (
        _hud_rect,
        _strip_rects,
    )
    from ling_agent.computer.types import Rect

    primary = _strip_rects(Rect(0, 0, 1920, 1080), 96)
    assert primary == (
        (0, 0, 1920, 5),
        (0, 1075, 1920, 5),
        (0, 0, 5, 1080),
        (1915, 0, 5, 1080),
    )
    secondary = _strip_rects(Rect(-1920, -200, 0, 880), 144)
    assert secondary == (
        (-1920, -200, 1920, 8),
        (-1920, 872, 1920, 8),
        (-1920, -200, 8, 1080),
        (-8, -200, 8, 1080),
    )
    halo = _strip_rects(Rect(0, 0, 1920, 1080), 96, 18)
    assert halo[0] == (0, 0, 1920, 18)
    assert _hud_rect(Rect(0, 0, 1920, 1080), 96) == (
        750,
        24,
        420,
        76,
    )


def test_overlay_physical_escape_detection() -> None:
    if sys.platform != "win32":
        return
    from ling_agent.computer.helper.overlay import (
        HC_ACTION,
        LLKHF_INJECTED,
        WM_KEYDOWN,
        _is_physical_escape,
    )

    assert _is_physical_escape(HC_ACTION, WM_KEYDOWN, 0x1B, 0)
    assert not _is_physical_escape(
        HC_ACTION,
        WM_KEYDOWN,
        0x1B,
        LLKHF_INJECTED,
    )
    assert not _is_physical_escape(HC_ACTION, WM_KEYDOWN, 0x41, 0)


def test_pick_existing_window_uses_primary_instance() -> None:
    if sys.platform != "win32":
        return
    from ling_agent.computer.helper.launch import _pick_existing_window

    exact = r"C:\App\app.exe"
    windows = [
        {
            "id": "w-1",
            "exePath": exact,
            "exeName": "app.exe",
            "visible": True,
            "minimized": True,
            "title": "Main",
            "className": "AppWindow",
            "bounds": {"width": 221, "height": 31},
            "processCreationTime": 100,
        },
        {
            "id": "w-2",
            "exePath": exact,
            "exeName": "app.exe",
            "visible": True,
            "minimized": False,
            "title": "Login",
            "className": "AppWindow",
            "bounds": {"width": 352, "height": 476},
            "processCreationTime": 200,
        },
        {
            "id": "w-3",
            "exePath": r"C:\Other\app.exe",
            "exeName": "app.exe",
            "visible": True,
            "minimized": False,
            "title": "Other",
            "className": "AppWindow",
            "bounds": {"width": 800, "height": 600},
            "processCreationTime": 300,
        },
        {
            "id": "w-ime",
            "exePath": exact,
            "exeName": "app.exe",
            "visible": False,
            "minimized": False,
            "title": "Default IME",
            "className": "IME",
            "bounds": {"width": 0, "height": 0},
            "processCreationTime": 50,
        },
        {
            "id": "w-tray",
            "exePath": exact,
            "exeName": "app.exe",
            "visible": False,
            "minimized": False,
            "title": "WxTrayIconMessageWindow",
            "className": "QtTrayWindow",
            "bounds": {"width": 800, "height": 600},
            "processCreationTime": 60,
        },
    ]
    picked = _pick_existing_window(windows, exact, "app.exe")
    assert picked is not None and picked["id"] == "w-1"
    hidden = _pick_existing_window([windows[1]], exact, "app.exe")
    assert hidden is not None and hidden["id"] == "w-2"
    packaged = _pick_existing_window(
        [
            {
                "id": "w-4",
                "exePath": r"C:\Program Files\WindowsApps\App.exe",
                "exeName": "app.exe",
                "visible": True,
                "minimized": False,
                "title": "App",
                "className": "AppWindow",
                "bounds": {"width": 800, "height": 600},
                "processCreationTime": 100,
            }
        ],
        exact,
        "app.exe",
    )
    assert packaged is not None and packaged["id"] == "w-4"
    assert _pick_existing_window([], exact, "app.exe") is None


def test_launch_executable_resolution() -> None:
    if sys.platform != "win32":
        return
    import tempfile

    from ling_agent.computer.helper.launch import (
        launch_app,
        resolve_executable,
    )
    from ling_agent.computer.types import ComputerError

    resolved = resolve_executable("notepad")
    assert resolved.name.lower() == "notepad.exe"
    assert resolved.is_file()
    with tempfile.NamedTemporaryFile(suffix=".sh") as script:
        try:
            resolve_executable(script.name)
        except ComputerError as exc:
            assert exc.code == "APP_LAUNCH_FAILED"
        else:
            raise AssertionError("shell scripts must not be launched")
    try:
        resolve_executable("ling-definitely-not-installed-xyz")
    except ComputerError as exc:
        assert exc.code == "APP_NOT_FOUND"
    else:
        raise AssertionError("missing app should not resolve")
    try:
        launch_app(app="cmd.exe", wait_for_window_ms=0)
    except ComputerError as exc:
        assert exc.code == "APP_BLOCKED"
    else:
        raise AssertionError("terminals must stay blocked")


def test_helper_restart_rate_limit() -> None:
    from ling_agent.computer.helper_client import _spawn_allowed

    assert _spawn_allowed([], 100.0) is True
    assert _spawn_allowed([99.0, 100.0], 100.0) is True
    assert _spawn_allowed([99.0, 100.0, 101.0], 102.0) is False
    assert _spawn_allowed([1.0, 2.0, 3.0], 100.0) is True


def test_window_observe_combines_text_and_screenshot() -> None:
    if sys.platform != "win32":
        return
    from ling_agent.computer.helper import main

    original_snapshot = main.snapshot_window
    original_capture = main.capture_window
    main.snapshot_window = lambda **_: {
        "snapshotId": "snap-1",
        "window": {"id": "w-1", "title": "Notes"},
        "tree": "document",
        "elements": [],
        "focusedElement": None,
        "selectedElements": [],
        "selectedText": "",
        "documentText": "hello",
        "complete": True,
        "truncated": False,
        "identityUnproven": False,
        "nodeCount": 1,
    }
    main.capture_window = lambda **_: {
        "captureId": "cap-1",
        "path": "C:\\temp\\shot.png",
        "width": 100,
        "height": 80,
        "window": {"id": "w-1", "title": "Notes"},
    }
    try:
        result = main._observe_window(
            {"window_id": "w-1"}
        )
        observation = main._take_observation(
            result["observationId"]
        )
    finally:
        main.snapshot_window = original_snapshot
        main.capture_window = original_capture
    assert result["snapshotId"] == "snap-1"
    assert result["captureId"] == "cap-1"
    assert result["documentText"] == "hello"
    assert observation["snapshotId"] == "snap-1"
    assert observation["captureId"] == "cap-1"


def test_window_observe_rejects_changed_window() -> None:
    if sys.platform != "win32":
        return
    from ling_agent.computer.helper import main
    from ling_agent.computer.types import ComputerError

    original_snapshot = main.snapshot_window
    original_capture = main.capture_window
    main.snapshot_window = lambda **_: {
        "snapshotId": "snap-1",
        "window": {"id": "w-1"},
        "tree": "",
        "elements": [],
        "complete": True,
        "truncated": False,
        "identityUnproven": False,
        "nodeCount": 0,
    }
    main.capture_window = lambda **_: {
        "captureId": "cap-1",
        "window": {"id": "w-2"},
    }
    try:
        try:
            main._observe_window({"window_id": "w-1"})
        except ComputerError as exc:
            assert exc.code == "WINDOW_CHANGED"
        else:
            raise AssertionError("changed window should be rejected")
    finally:
        main.snapshot_window = original_snapshot
        main.capture_window = original_capture


def test_sidecar_entry_helper_branch() -> None:
    if sys.platform != "win32":
        return
    result = subprocess.run(
        [
            sys.executable,
            "ling_sidecar_entry.py",
            "--computer-helper",
            "--self-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert isinstance(payload["windows"], list)


if __name__ == "__main__":
    test_list_windows_shape()
    test_self_test_subprocess()
    test_real_uia_snapshot_opt_in()
    test_helper_client_allows_concurrent_requests()
    test_overlay_strips_negative_origin_and_dpi()
    test_overlay_physical_escape_detection()
    test_pick_existing_window_uses_primary_instance()
    test_launch_executable_resolution()
    test_helper_restart_rate_limit()
    test_window_observe_combines_text_and_screenshot()
    test_window_observe_rejects_changed_window()
    test_sidecar_entry_helper_branch()
    print("computer helper tests passed")

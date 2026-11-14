"""Tests for the sidecar-side computer service."""

from __future__ import annotations

import asyncio

from ling_agent import (
    ComputerEffect,
    ComputerService,
    ComputerUseConfig,
)
from ling_agent.computer.types import ComputerError


def _config(**overrides) -> ComputerUseConfig:
    return ComputerUseConfig(audit_enabled=False, **overrides)


class FakeHelper:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict, float | None]] = []
        self.closed = False
        self.windows: list[dict] = []
        self.act_error: ComputerError | None = None

    async def request(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        self.requests.append((method, params or {}, timeout))
        if method in ("uia.snapshot", "window.observe"):
            include_screenshot = bool(
                params.get("include_screenshot", method == "uia.snapshot")
            )
            include_text = bool(
                params.get("include_text", True)
            )
            display_width = int(
                params.get("max_width", 1280)
            )
            result = {
                "observationId": "obs-1",
                "windowId": "w-1",
                "snapshotId": "snap-1" if include_text else "",
                "captureId": "cap-1" if include_screenshot else "",
                "path": (
                    "C:\\temp\\cap-1.png"
                    if include_screenshot
                    else ""
                ),
                "width": display_width if include_screenshot else None,
                "height": round(display_width * 9 / 16)
                if include_screenshot
                else None,
                "window": {
                    "id": "w-1",
                    "title": "Notepad",
                    "app": "notepad.exe",
                },
                "tree": 'window "Notepad"',
                "elements": [],
                "complete": True,
                "truncated": False,
                "identityUnproven": False,
                "nodeCount": 1,
            }
            if method == "uia.snapshot":
                result.pop("observationId")
                result.pop("windowId")
                result.pop("captureId")
                result.pop("path")
                result.pop("width")
                result.pop("height")
                result.pop("snapshotId")
                result["snapshotId"] = "snap-1"
            return result
        if method == "uia.find":
            return {
                "snapshotId": "snap-1",
                "windowId": "w-1",
                "element": {"ref": "@snap-1:e1", "role": "button"},
            }
        if method == "control.begin":
            return {
                "active": True,
                "windowId": params.get("window_id", ""),
                "displays": [],
            }
        if method == "capture.window":
            return {
                "captureId": "cap-1",
                "path": "C:\\temp\\cap-1.png",
                "width": 640,
                "height": 360,
                "originalWidth": 1280,
                "originalHeight": 720,
                "scale": 0.5,
                "mimeType": "image/png",
                "method": "printwindow",
                "bytes": 1234,
                "window": {
                    "id": params.get("window_id", "w-1"),
                    "title": "Notepad",
                    "app": "notepad.exe",
                    "exeName": "notepad.exe",
                    "controllable": True,
                },
            }
        if method == "window.focus":
            return {
                "window": {
                    "id": "w-1",
                    "title": "Notepad",
                    "app": "notepad.exe",
                    "exeName": "notepad.exe",
                    "controllable": True,
                },
                "foreground": True,
                "restored": True,
            }
        if method == "app.launch":
            return {
                "pid": 4321,
                "exe": "C:\\Windows\\System32\\notepad.exe",
                "arguments": list(params.get("arguments") or ()),
                "window": {
                    "id": "w-9",
                    "title": "Untitled - Notepad",
                    "app": "notepad.exe",
                    "exeName": "notepad.exe",
                    "controllable": True,
                },
                "reused": False,
                "timedOut": False,
            }
        if method == "input.act":
            if self.act_error is not None:
                raise self.act_error
            return {
                "actions": [
                    {
                        "type": "click",
                        "ref": "@snap-1:e1",
                        "ok": True,
                        "verified": False,
                    }
                ],
                "delivered": True,
                "mayHaveTakenEffect": True,
            }
        if method == "control.end":
            return {"stopped": True}
        return {
            "windows": list(self.windows),
            "displays": [],
            "helper": {"pid": 1, "version": "0.1.0"},
            "total": len(self.windows),
            "truncated": False,
        }

    async def close(self, timeout: float = 0.4) -> None:
        self.closed = True


def test_service_lists_windows() -> None:
    helper = FakeHelper()
    service = ComputerService(_config(), helper=helper)
    result = asyncio.run(
        service.list_windows(include_all=True, query="edge")
    )
    assert result["total"] == 0
    assert helper.requests == [
        (
            "windows.list",
            {
                "include_all": True,
                "query": "edge",
                "max_windows": 100,
            },
            30.0,
        )
    ]
    asyncio.run(service.close())
    assert helper.closed is True


def test_service_observes_window() -> None:
    helper = FakeHelper()
    service = ComputerService(_config(), helper=helper)
    result = asyncio.run(
        service.observe(
            window_id="w-1",
            max_depth=6,
            max_nodes=120,
            max_chars=12_000,
            query="save",
        )
    )
    assert result["snapshotId"] == "snap-1"
    assert result["observationId"] == "obs-1"
    assert result["captureId"] == "cap-1"
    assert helper.requests == [
        (
            "window.observe",
            {
                "window_id": "w-1",
                "include_screenshot": True,
                "include_text": True,
                "max_width": 1280,
                "max_depth": 6,
                "max_nodes": 120,
                "max_chars": 12_000,
                "query": "save",
            },
            30.0,
        )
    ]
    found = asyncio.run(
        service.find_ref(
            window_id="w-1",
            snapshot_id="snap-1",
            ref="@snap-1:e1",
        )
    )
    assert found["element"]["role"] == "button"


def test_service_control_session_lifecycle() -> None:
    helper = FakeHelper()
    helper.windows = [
        {
            "id": "w-1",
            "title": "Notepad",
            "app": "notepad.exe",
            "exeName": "notepad.exe",
            "controllable": True,
        }
    ]
    events: list[tuple[dict | None, str]] = []

    async def on_updated(session: dict | None, reason: str) -> None:
        events.append((session, reason))

    service = ComputerService(
        _config(),
        helper=helper,
        on_updated=on_updated,
    )

    async def scenario() -> None:
        await service.list_windows()
        result = await service.act(
            window_id="w-1",
            snapshot_id="snap-1",
            actions=[{"type": "click", "ref": "@snap-1:e1"}],
        )
        assert result["delivered"] is True
        assert result["observation"]["observationId"] == "obs-1"
        status = service.status()
        assert status is not None
        assert status["windowId"] == "w-1"
        effect = ComputerEffect(
            operation="act",
            classification="mutating",
            window_id="w-1",
        )
        assert service.is_covered(effect) is True
        assert service.deny_reason(effect) is None
        assert await service.stop("test") is True
        assert service.status() is None
        assert await service.stop("test") is False

    asyncio.run(scenario())
    methods = [request[0] for request in helper.requests]
    assert methods == [
        "windows.list",
        "control.begin",
        "input.act",
        "window.observe",
        "control.end",
    ]
    assert events[0][1] == "started"
    assert events[-1][0] is None
    assert events[-1][1] == "test"


def test_service_captures_window_without_session() -> None:
    helper = FakeHelper()
    service = ComputerService(_config(), helper=helper)

    async def scenario() -> None:
        result = await service.screenshot(
            window_id="w-1",
            max_width=1280,
        )
        assert result["captureId"] == "cap-1"
        assert result["width"] == 640
        assert service.status() is None

    asyncio.run(scenario())
    methods = [request[0] for request in helper.requests]
    assert methods == ["capture.window"]
    assert "control.begin" not in methods


def test_action_refresh_preserves_observation_width() -> None:
    helper = FakeHelper()
    helper.windows = [
        {
            "id": "w-1",
            "title": "Notepad",
            "app": "notepad.exe",
            "exeName": "notepad.exe",
            "controllable": True,
        }
    ]
    service = ComputerService(_config(), helper=helper)

    async def scenario() -> None:
        await service.list_windows()
        observed = await service.observe(
            window_id="w-1",
            max_width=1400,
        )
        await service.act(
            window_id="w-1",
            observation_id=observed["observationId"],
            actions=[{"type": "press_key", "key": "enter"}],
        )
        await service.stop("test")

    asyncio.run(scenario())
    refresh = [
        request for request in helper.requests
        if request[0] == "window.observe"
    ][-1]
    assert refresh[1]["max_width"] == 1400


def test_service_focuses_window() -> None:
    helper = FakeHelper()
    helper.windows = [
        {
            "id": "w-1",
            "title": "Notepad",
            "app": "notepad.exe",
            "exeName": "notepad.exe",
            "controllable": True,
        }
    ]
    events: list[tuple[dict | None, str]] = []

    async def on_updated(session: dict | None, reason: str) -> None:
        events.append((session, reason))

    service = ComputerService(
        _config(),
        helper=helper,
        on_updated=on_updated,
    )

    async def scenario() -> None:
        await service.list_windows()
        result = await service.focus(window_id="w-1", call_id="call-1")
        assert result["foreground"] is True
        assert result["restored"] is True
        assert service.status() is not None
        assert service.status()["windowId"] == "w-1"
        await service.stop("test")

    asyncio.run(scenario())
    methods = [request[0] for request in helper.requests]
    assert methods == [
        "windows.list",
        "control.begin",
        "window.focus",
        "control.end",
    ]
    assert events[0][1] == "started"
    assert events[1][1] == "window"
    assert events[-1] == (None, "test")


def test_service_launches_app() -> None:
    helper = FakeHelper()
    events: list[tuple[dict | None, str]] = []

    async def on_updated(session: dict | None, reason: str) -> None:
        events.append((session, reason))

    service = ComputerService(
        _config(),
        helper=helper,
        on_updated=on_updated,
    )

    async def scenario() -> None:
        result = await service.launch(
            app="notepad.exe",
            arguments=["-n"],
            wait_for_window_ms=100,
        )
        assert result["window"]["id"] == "w-9"
        status = service.status()
        assert status is not None
        assert status["windowId"] == "w-9"
        assert status["app"] == "notepad.exe"
        assert status["windowTitle"] == "Untitled - Notepad"
        assert service.is_covered(
            ComputerEffect(
                operation="launch",
                classification="launch",
                app="notepad.exe",
            )
        )
        await service.stop("test")

    asyncio.run(scenario())
    methods = [request[0] for request in helper.requests]
    assert methods == [
        "control.begin",
        "app.launch",
        "control.end",
    ]
    assert events[0][1] == "started"
    assert events[1][1] == "window"
    assert events[-1] == (None, "test")


def test_helper_loss_clears_session() -> None:
    helper = FakeHelper()
    helper.windows = [
        {
            "id": "w-1",
            "title": "Notepad",
            "app": "notepad.exe",
            "exeName": "notepad.exe",
            "controllable": True,
        }
    ]
    events: list[tuple[dict | None, str]] = []

    async def on_updated(session: dict | None, reason: str) -> None:
        events.append((session, reason))

    service = ComputerService(
        _config(),
        helper=helper,
        on_updated=on_updated,
    )

    async def scenario() -> None:
        await service.list_windows()
        await service.act(
            window_id="w-1",
            snapshot_id="snap-1",
            actions=[{"type": "click", "ref": "@snap-1:e1"}],
        )
        assert service.status() is not None
        await service._handle_helper_lost()
        assert service.status() is None

    asyncio.run(scenario())
    assert events[-1] == (None, "helper_lost")
    methods = [request[0] for request in helper.requests]
    assert "input.act" in methods
    assert methods[-1] == "window.observe"


def test_service_stops_on_helper_error() -> None:
    helper = FakeHelper()
    helper.windows = [
        {
            "id": "w-1",
            "title": "Notepad",
            "app": "notepad.exe",
            "exeName": "notepad.exe",
            "controllable": True,
        }
    ]
    helper.act_error = ComputerError(
        "HELPER_TIMEOUT",
        "The helper timed out.",
    )
    events: list[tuple[dict | None, str]] = []

    async def on_updated(session: dict | None, reason: str) -> None:
        events.append((session, reason))

    service = ComputerService(
        _config(),
        helper=helper,
        on_updated=on_updated,
    )

    async def scenario() -> None:
        await service.list_windows()
        try:
            await service.act(
                window_id="w-1",
                snapshot_id="snap-1",
                actions=[{"type": "click", "ref": "@snap-1:e1"}],
            )
        except ComputerError as exc:
            assert exc.code == "HELPER_TIMEOUT"
        else:
            raise AssertionError("helper error should stop the action")
        assert service.status() is None

    asyncio.run(scenario())
    methods = [request[0] for request in helper.requests]
    assert methods == [
        "windows.list",
        "control.begin",
        "input.act",
        "control.end",
    ]
    assert events[0][1] == "started"
    assert events[-1] == (None, "helper_error")


def test_no_effect_error_preserves_control_session() -> None:
    helper = FakeHelper()
    helper.windows = [
        {
            "id": "w-1",
            "title": "Notepad",
            "app": "notepad.exe",
            "exeName": "notepad.exe",
            "controllable": True,
        }
    ]
    helper.act_error = ComputerError(
        "INVALID_ARGS",
        "Unsupported key: ctrl+s",
        may_have_taken_effect=False,
    )
    service = ComputerService(_config(), helper=helper)

    async def scenario() -> None:
        await service.list_windows()
        try:
            await service.act(
                window_id="w-1",
                snapshot_id="snap-1",
                actions=[{"type": "press_key", "key": "ctrl+s"}],
            )
        except ComputerError as exc:
            assert exc.code == "INVALID_ARGS"
        else:
            raise AssertionError("invalid action should fail")
        assert service.status() is not None
        await service.stop("test")

    asyncio.run(scenario())
    methods = [request[0] for request in helper.requests]
    assert methods == [
        "windows.list",
        "control.begin",
        "input.act",
        "control.end",
    ]


def test_pending_grant_covers_parallel_actions() -> None:
    service = ComputerService(_config(), helper=FakeHelper())
    mutating = ComputerEffect(
        operation="act",
        classification="mutating",
        window_id="w-1",
        action="click",
    )
    readonly = ComputerEffect(
        operation="observe",
        classification="readonly",
        window_id="w-1",
    )
    assert service.is_covered(mutating) is False
    service.note_approval_granted(mutating)
    assert service.is_covered(mutating) is True
    assert service.is_covered(readonly) is False
    service.note_approval_granted(readonly)
    assert service.is_covered(readonly) is False


def test_service_denies_unknown_window() -> None:
    service = ComputerService(_config(), helper=FakeHelper())
    effect = ComputerEffect(
        operation="act",
        classification="mutating",
        window_id="w-missing",
    )
    assert service.deny_reason(effect) is not None
    assert service.is_covered(effect) is False


def test_disabled_service_rejects_calls() -> None:
    helper = FakeHelper()
    service = ComputerService(
        _config(enabled=False),
        helper=helper,
    )
    assert service.enabled is False
    try:
        asyncio.run(service.list_windows())
    except ComputerError as exc:
        assert exc.code == "COMPUTER_DISABLED"
    else:
        raise AssertionError("disabled service should raise")
    assert helper.requests == []


if __name__ == "__main__":
    test_service_lists_windows()
    test_service_observes_window()
    test_service_control_session_lifecycle()
    test_service_captures_window_without_session()
    test_action_refresh_preserves_observation_width()
    test_service_focuses_window()
    test_service_launches_app()
    test_helper_loss_clears_session()
    test_service_stops_on_helper_error()
    test_no_effect_error_preserves_control_session()
    test_pending_grant_covers_parallel_actions()
    test_service_denies_unknown_window()
    test_disabled_service_rejects_calls()
    print("computer service tests passed")

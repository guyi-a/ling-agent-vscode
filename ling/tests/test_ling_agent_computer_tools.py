"""Tests for the read-only computer_list tool."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path

from ling_agent import (
    ApprovalMode,
    CodingAgent,
    HarnessConfig,
    ToolTimelineItem,
    Workspace,
    build_system_prompt,
)
from ling_agent.computer.types import ComputerError
from ling_agent.tools.computer import create_computer_tools
from ling_ai.types import Model, ToolCall
from ling_core import ToolContext
from ling_rpc.ui_events import _tool_entry


class FakeService:
    def __init__(self, error: ComputerError | None = None) -> None:
        self.error = error
        self.calls: list[tuple] = []
        self.screenshot_path = ""

    async def list_windows(
        self,
        *,
        include_all: bool = False,
        query: str = "",
    ) -> dict:
        self.calls.append((include_all, query))
        if self.error is not None:
            raise self.error
        return {
            "windows": [
                {
                    "id": "w-1",
                    "title": "Untitled - Notepad",
                    "app": "notepad.exe",
                    "pid": 1234,
                    "controllable": True,
                }
            ],
            "displays": [{"index": 0, "primary": True}],
            "helper": {"pid": 99, "version": "0.1.0"},
            "total": 1,
            "truncated": False,
        }

    async def observe(
        self,
        *,
        window_id: str,
        include_screenshot: bool = True,
        include_text: bool = True,
        max_width: int = 1280,
        max_depth: int = 8,
        max_nodes: int = 300,
        max_chars: int = 30_000,
        query: str = "",
        call_id: str = "",
    ) -> dict:
        self.calls.append(
            (
                "observe",
                window_id,
                include_screenshot,
                include_text,
                max_width,
                max_depth,
                max_nodes,
                max_chars,
                query,
                call_id,
            )
        )
        if self.error is not None:
            raise self.error
        return {
            "snapshotId": "snap-test",
            "observationId": "obs-test",
            "captureId": "cap-test",
            "path": self.screenshot_path,
            "window": {
                "id": window_id,
                "title": "Untitled - Notepad",
                "app": "notepad.exe",
            },
            "tree": 'window "Untitled - Notepad"\n  [@snap-test:e1] button "OK"',
            "elements": [
                {
                    "ref": "@snap-test:e1",
                    "role": "button",
                    "name": "OK",
                }
            ],
            "complete": True,
            "truncated": False,
            "identityUnproven": False,
            "nodeCount": 3,
        }

    async def screenshot(
        self,
        *,
        window_id: str,
        max_width: int = 1280,
        call_id: str = "",
    ) -> dict:
        self.calls.append(
            ("screenshot", window_id, max_width, call_id)
        )
        if self.error is not None:
            raise self.error
        return {
            "captureId": "cap-test",
            "path": self.screenshot_path,
            "width": 10,
            "height": 10,
            "originalWidth": 20,
            "originalHeight": 20,
            "scale": 0.5,
            "method": "printwindow",
            "window": {
                "id": window_id,
                "title": "Untitled - Notepad",
                "app": "notepad.exe",
            },
        }

    async def focus(
        self,
        *,
        window_id: str,
        call_id: str = "",
    ) -> dict:
        self.calls.append(("focus", window_id, call_id))
        if self.error is not None:
            raise self.error
        return {
            "window": {
                "id": window_id,
                "title": "Untitled - Notepad",
                "app": "notepad.exe",
            },
            "foreground": True,
            "restored": True,
        }

    async def launch(
        self,
        *,
        app: str,
        arguments: list[str] | None = None,
        wait_for_window_ms: int = 5000,
        call_id: str = "",
    ) -> dict:
        self.calls.append(
            ("launch", app, arguments, wait_for_window_ms, call_id)
        )
        if self.error is not None:
            raise self.error
        return {
            "pid": 4321,
            "window": {
                "id": "w-9",
                "title": "Untitled - Notepad",
                "app": "notepad.exe",
            },
            "reused": False,
            "timedOut": False,
        }

    async def act(
        self,
        *,
        window_id: str,
        observation_id: str = "",
        snapshot_id: str = "",
        capture_id: str = "",
        actions: list[dict],
        call_id: str = "",
    ) -> dict:
        self.calls.append(
            (
                "act",
                window_id,
                observation_id,
                snapshot_id,
                capture_id,
                actions,
                call_id,
            )
        )
        if self.error is not None:
            raise self.error
        return {
            "actions": [
                {
                    "type": "click",
                    "ref": actions[0].get("ref"),
                    "ok": True,
                    "verified": False,
                }
            ],
            "delivered": True,
            "mayHaveTakenEffect": True,
            "observation": {
                "observationId": "obs-next",
                "snapshotId": "snap-next",
                "captureId": "cap-next",
                "window": {
                    "id": window_id,
                    "title": "Untitled - Notepad",
                    "app": "notepad.exe",
                },
            },
        }

    async def stop(self, reason: str = "tool") -> bool:
        self.calls.append(("stop", reason))
        if self.error is not None:
            raise self.error
        return True


def _tool_named(service: FakeService, name: str):
    return next(
        tool
        for tool in create_computer_tools(service, HarnessConfig())
        if tool.definition.name == name
    )


def _run(
    service: FakeService,
    arguments: dict,
    name: str = "computer_list",
):
    return asyncio.run(
        _tool_named(service, name).handler(
            ToolCall(
                id="call-1",
                name=name,
                arguments=arguments,
            ),
            ToolContext(step=1),
        )
    )


def test_computer_list_returns_windows() -> None:
    service = FakeService()
    execution = _run(service, {"query": "note", "include_all": True})
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["ok"] is True
    assert payload["windows"][0]["id"] == "w-1"
    assert payload["displays"][0]["primary"] is True
    assert service.calls == [(True, "note")]
    assert execution.details["kind"] == "computer"
    assert execution.details["operation"] == "list"


def test_computer_observe_returns_snapshot() -> None:
    service = FakeService()
    execution = _run(
        service,
        {"window_id": "w-1", "max_depth": 5},
        name="computer_observe",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["ok"] is True
    assert payload["snapshotId"] == "snap-test"
    assert payload["elements"][0]["ref"] == "@snap-test:e1"
    assert service.calls == [
        (
            "observe",
            "w-1",
            True,
            True,
            1280,
            5,
            300,
            30_000,
            "",
            "call-1",
        )
    ]
    assert execution.details["kind"] == "computer"
    assert execution.details["operation"] == "observe"
    assert execution.details["snapshotId"] == "snap-test"


def test_computer_observe_requires_window_id() -> None:
    execution = _run(
        FakeService(),
        {},
        name="computer_observe",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert payload["code"] == "COMPUTER_ERROR"


def test_computer_observe_returns_combined_state_image() -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
        "AAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
    )
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "observation.png"
        path.write_bytes(png)
        service = FakeService()
        service.screenshot_path = str(path)
        execution = _run(
            service,
            {"window_id": "w-1"},
            name="computer_observe",
        )
        assert execution.is_error is False
        assert isinstance(execution.output, list)
        assert len(execution.output) == 2
        assert execution.output[1].type == "image"
        assert execution.details["observationId"] == "obs-test"
        assert execution.details["captureId"] == "cap-test"


def test_computer_screenshot_returns_image() -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
        "AAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
    )
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "shot.png"
        path.write_bytes(png)
        service = FakeService()
        service.screenshot_path = str(path)
        execution = _run(
            service,
            {"window_id": "w-1", "max_width": 640},
            name="computer_screenshot",
        )
        assert execution.is_error is False
        assert isinstance(execution.output, list)
        assert len(execution.output) == 2
        assert execution.output[0].type == "text"
        image = execution.output[1]
        assert image.type == "image"
        assert image.mime_type == "image/png"
        assert base64.b64decode(image.data) == png
        assert execution.details["operation"] == "screenshot"
        assert execution.details["captureId"] == "cap-test"
        assert execution.details["width"] == 10
        assert execution.details["scale"] == 0.5
        assert service.calls == [
            ("screenshot", "w-1", 640, "call-1")
        ]


def test_computer_focus_returns_foreground() -> None:
    service = FakeService()
    execution = _run(
        service,
        {"window_id": "w-1"},
        name="computer_focus",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["ok"] is True
    assert payload["foreground"] is True
    assert payload["restored"] is True
    assert execution.details["operation"] == "focus"
    assert execution.details["windowId"] == "w-1"
    assert execution.details["foreground"] is True
    assert service.calls == [("focus", "w-1", "call-1")]


def test_computer_focus_requires_window_id() -> None:
    execution = _run(
        FakeService(),
        {},
        name="computer_focus",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert payload["code"] == "COMPUTER_ERROR"


def test_computer_launch_returns_window() -> None:
    service = FakeService()
    execution = _run(
        service,
        {
            "app": "notepad.exe",
            "arguments": ["-n"],
            "wait_for_window_ms": 3000,
        },
        name="computer_launch",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["ok"] is True
    assert payload["window"]["id"] == "w-9"
    assert execution.details["operation"] == "launch"
    assert execution.details["windowId"] == "w-9"
    assert execution.details["pid"] == 4321
    assert service.calls == [
        ("launch", "notepad.exe", ["-n"], 3000, "call-1")
    ]


def test_computer_launch_requires_app() -> None:
    execution = _run(
        FakeService(),
        {},
        name="computer_launch",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert payload["code"] == "COMPUTER_ERROR"


def test_computer_act_returns_steps() -> None:
    service = FakeService()
    execution = _run(
        service,
        {
            "window_id": "w-1",
            "observation_id": "obs-test",
            "actions": [{"type": "click", "ref": "@snap-test:e1"}],
        },
        name="computer_act",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["ok"] is True
    assert payload["delivered"] is True
    assert payload["actions"][0]["type"] == "click"
    assert execution.details["operation"] == "act"
    assert service.calls[0][0] == "act"
    assert service.calls[0][2] == "obs-test"


def test_computer_act_passes_capture_id_for_coordinate_actions() -> None:
    service = FakeService()
    execution = _run(
        service,
        {
            "window_id": "w-1",
            "observation_id": "obs-test",
            "actions": [{"type": "click_at", "x": 10, "y": 20}],
        },
        name="computer_act",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["ok"] is True
    assert service.calls[0][2] == "obs-test"
    assert execution.details["captureId"] == "cap-next"


def test_computer_act_requires_capture_for_coordinates() -> None:
    execution = _run(
        FakeService(),
        {
            "window_id": "w-1",
            "actions": [{"type": "click_at", "x": 10, "y": 20}],
        },
        name="computer_act",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert "observation_id" in payload["message"]


def test_computer_act_requires_ref_for_uia_actions() -> None:
    execution = _run(
        FakeService(),
        {
            "window_id": "w-1",
            "snapshot_id": "snap-test",
            "actions": [{"type": "click"}],
        },
        name="computer_act",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert "requires a ref" in payload["message"]


def test_computer_launch_schema() -> None:
    tool = _tool_named(FakeService(), "computer_launch")
    parameters = tool.definition.parameters
    assert parameters["required"] == ["app"]
    assert parameters["properties"]["app"]["type"] == "string"
    assert (
        parameters["properties"]["wait_for_window_ms"]["maximum"]
        == 15000
    )
    assert (
        parameters["properties"]["arguments"]["items"]["type"]
        == "string"
    )


def test_computer_act_schema_supports_input_actions() -> None:
    tool = _tool_named(FakeService(), "computer_act")
    actions_schema = tool.definition.parameters["properties"]["actions"]
    item = actions_schema["items"]
    assert set(item["properties"]["type"]["enum"]) == {
        "click",
        "click_at",
        "set_value",
        "type",
        "press_key",
        "focus",
        "toggle",
        "expand",
        "collapse",
        "select",
        "scroll",
        "scroll_at",
        "drag",
        "drag_at",
    }
    assert item["required"] == ["type"]
    assert "capture_id" in tool.definition.parameters["properties"]
    assert "observation_id" in tool.definition.parameters["properties"]
    assert "snapshot_id" not in tool.definition.parameters["required"]
    assert tool.definition.parameters["required"] == [
        "window_id",
        "observation_id",
        "actions",
    ]
    assert actions_schema["maxItems"] == 1
    assert item["properties"]["text"]["maxLength"] == 4000
    assert item["properties"]["direction"]["enum"] == [
        "up",
        "down",
        "left",
        "right",
    ]
    assert item["properties"]["amount"]["maximum"] == 10
    assert "target_ref" in item["properties"]


def test_computer_stop_returns_stopped() -> None:
    service = FakeService()
    execution = _run(
        service,
        {"reason": "test"},
        name="computer_stop",
    )
    payload = json.loads(execution.output)
    assert execution.is_error is False
    assert payload["stopped"] is True
    assert execution.details["operation"] == "stop"
    assert service.calls == [("stop", "test")]


def test_computer_list_maps_computer_error() -> None:
    service = FakeService(
        ComputerError(
            "HELPER_UNAVAILABLE",
            "helper is not running",
            hint="retry later",
        )
    )
    execution = _run(service, {})
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert payload["ok"] is False
    assert payload["code"] == "HELPER_UNAVAILABLE"
    assert payload["hint"] == "retry later"


def test_computer_list_rejects_non_bool_include_all() -> None:
    execution = _run(FakeService(), {"include_all": "yes"})
    payload = json.loads(execution.output)
    assert execution.is_error is True
    assert payload["code"] == "COMPUTER_ERROR"


def test_coding_agent_registers_computer_list() -> None:
    async def stream(*args, **kwargs):
        raise AssertionError("stream must not run")

    with tempfile.TemporaryDirectory() as temp:
        agent = CodingAgent(
            model=Model(
                id="test",
                provider="test",
                api="openai-completions",
            ),
            stream_fn=stream,
            workspace_root=Path(temp),
            computer_service=FakeService(),
        )
        names = {
            tool.definition.name
            for tool in agent._create_tools(ApprovalMode.PLAN)
        }
        assert "computer_list" in names
        assert "computer_observe" in names
        assert "computer_screenshot" in names
        assert "computer_stop" in names
        assert "computer_act" not in names
        assert "computer_launch" not in names
        assert "computer_focus" not in names
        asyncio.run(agent.close())

        without = CodingAgent(
            model=Model(
                id="test",
                provider="test",
                api="openai-completions",
            ),
            stream_fn=stream,
            workspace_root=Path(temp),
        )
        without_names = {
            tool.definition.name
            for tool in without._create_tools(ApprovalMode.MANUAL)
        }
        assert "computer_list" not in without_names
        assert "computer_observe" not in without_names
        assert "computer_act" not in without_names
        assert "computer_launch" not in without_names
        assert "computer_focus" not in without_names
        assert "computer_stop" not in without_names
        asyncio.run(without.close())


def test_computer_prompt_policy() -> None:
    with tempfile.TemporaryDirectory() as temp:
        prompt = build_system_prompt(
            Workspace(Path(temp)),
            tool_names=("computer_list", "read_file"),
        )
        assert "## Computer use" in prompt.system_prompt
        assert "computer" in prompt.sources


def test_computer_tool_ui_entry() -> None:
    item = ToolTimelineItem(
        id="call-1",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=1,
        name="computer_observe",
        arguments={"window_id": "w-1"},
        output='{"ok": true}',
        details={
            "kind": "computer",
            "operation": "observe",
            "windowId": "w-1",
            "snapshotId": "snap-test",
            "nodeCount": 3,
            "complete": True,
            "preview": 'window "Notepad"',
        },
        status="completed",
    )
    entry = _tool_entry(item, waiting_for_approval=False)
    assert entry["tool"]["action"] == "Computer"
    assert entry["tool"]["target"] == "Observe w-1 · 3 nodes"
    detail = entry["tool"]["detail"]
    assert detail["kind"] == "computer"
    assert detail["snapshotId"] == "snap-test"
    assert detail["nodeCount"] == 3


if __name__ == "__main__":
    test_computer_list_returns_windows()
    test_computer_observe_returns_snapshot()
    test_computer_observe_requires_window_id()
    test_computer_observe_returns_combined_state_image()
    test_computer_screenshot_returns_image()
    test_computer_focus_returns_foreground()
    test_computer_focus_requires_window_id()
    test_computer_launch_returns_window()
    test_computer_launch_requires_app()
    test_computer_act_returns_steps()
    test_computer_launch_schema()
    test_computer_act_schema_supports_input_actions()
    test_computer_stop_returns_stopped()
    test_computer_list_maps_computer_error()
    test_computer_list_rejects_non_bool_include_all()
    test_coding_agent_registers_computer_list()
    test_computer_prompt_policy()
    test_computer_tool_ui_entry()
    print("computer tool tests passed")

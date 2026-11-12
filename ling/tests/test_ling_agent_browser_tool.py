"""Tests for browser_bridge tool, effects, and UI mapping."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from ling_agent import (
    ApprovalMode,
    BrowserEffect,
    CodingAgent,
    HarnessConfig,
    ToolTimelineItem,
    Workspace,
    approval_reason,
    build_system_prompt,
    classify_browser_action,
    derive_effect,
)
from ling_agent.approvals import create_approval_hook
from ling_agent.effects import plan_mode_denial
from ling_agent.browser_bridge import MAX_RESULT_BYTES
from ling_agent.tools.browser import _cap_json, create_browser_tools
from ling_ai.types import Model, ToolCall
from ling_core import PreToolUse, ToolContext
from ling_rpc.ui_events import _tool_entry


class FakeBridge:
    def __init__(self) -> None:
        self.started = 0
        self.ready_waits = 0
        self.calls: list[tuple[str, tuple, dict]] = []

    async def ensure_started(self) -> None:
        self.started += 1

    async def wait_until_ready(self, timeout, cancel_event=None) -> bool:
        self.ready_waits += 1
        return True

    def extension_status(self) -> dict:
        return {
            "ready": True,
            "sessions_count": 1,
            "browser_ids": ["browser-1"],
            "extension_versions": ["0.0.6"],
        }

    def list_sessions(self) -> list[dict]:
        return [{"browserId": "browser-1"}]

    def list_pages(self, browser_id: str) -> list[dict]:
        return [{"pageId": "page-1", "browserId": browser_id}]

    async def _result(self, name: str, *args, **kwargs) -> dict:
        self.calls.append((name, args, kwargs))
        return {"method": name, "args": list(args)}

    async def open_tab(self, *args, **kwargs):
        return await self._result("open_tab", *args, **kwargs)

    async def focus_page(self, *args, **kwargs):
        return await self._result("focus_page", *args, **kwargs)

    async def close_tab(self, *args, **kwargs):
        return await self._result("close_tab", *args, **kwargs)

    async def read_state(self, *args, **kwargs):
        return await self._result("read_state", *args, **kwargs)

    async def click(self, *args, **kwargs):
        return await self._result("click", *args, **kwargs)

    async def type_text(self, *args, **kwargs):
        return await self._result("type_text", *args, **kwargs)

    async def press(self, *args, **kwargs):
        return await self._result("press", *args, **kwargs)

    async def scroll(self, *args, **kwargs):
        return await self._result("scroll", *args, **kwargs)

    async def wait_for(self, *args, **kwargs):
        return await self._result("wait_for", *args, **kwargs)

    async def go_back(self, *args, **kwargs):
        return await self._result("go_back", *args, **kwargs)

    async def reload(self, *args, **kwargs):
        return await self._result("reload", *args, **kwargs)

    async def extract(self, *args, **kwargs):
        return await self._result("extract", *args, **kwargs)

    async def describe_element(self, *args, **kwargs):
        return await self._result("describe_element", *args, **kwargs)

    async def execute_script(self, *args, **kwargs):
        return await self._result("execute_script", *args, **kwargs)


def _tool(bridge: FakeBridge):
    return create_browser_tools(bridge, HarnessConfig())[0]


def _run(bridge: FakeBridge, arguments: dict):
    tool = _tool(bridge)
    return asyncio.run(
        tool.handler(
            ToolCall(
                id="call-1",
                name="browser_bridge",
                arguments=arguments,
            ),
            ToolContext(step=1),
        )
    )


def test_extension_status_and_list_pages() -> None:
    bridge = FakeBridge()
    execution = _run(bridge, {"action": "extension_status"})
    payload = json.loads(execution.output)
    assert payload["ok"] is True
    assert payload["status"]["ready"] is True
    assert bridge.started == 1
    assert bridge.ready_waits == 1
    assert execution.details["kind"] == "browser"

    execution = _run(
        bridge,
        {"action": "list_pages", "browser_id": "browser-1"},
    )
    assert json.loads(execution.output)["pages"][0]["pageId"] == "page-1"


def test_open_tab_and_click_arguments() -> None:
    bridge = FakeBridge()
    execution = _run(
        bridge,
        {
            "action": "open_tab",
            "browser_id": "browser-1",
            "url": "https://example.com",
            "active": False,
        },
    )
    payload = json.loads(execution.output)
    assert payload["data"]["method"] == "open_tab"
    assert bridge.calls[0][1] == (
        "browser-1",
        "https://example.com",
        False,
        None,
    )
    assert execution.details["url"] == "https://example.com"

    execution = _run(
        bridge,
        {
            "action": "click",
            "browser_id": "browser-1",
            "page_id": "page-1",
            "index": 3,
        },
    )
    assert json.loads(execution.output)["data"]["method"] == "click"
    assert bridge.calls[1][1] == ("browser-1", "page-1", 3, "click", None)


def test_invalid_arguments_return_tool_errors() -> None:
    bridge = FakeBridge()
    unknown = _run(bridge, {"action": "nope"})
    assert unknown.is_error is True
    assert "unknown browser action" in unknown.output

    missing = _run(bridge, {"action": "list_pages"})
    assert missing.is_error is True
    assert "browser_id" in missing.output

    missing_text = _run(
        bridge,
        {
            "action": "type",
            "browser_id": "browser-1",
            "page_id": "page-1",
            "index": 1,
        },
    )
    assert missing_text.is_error is True
    assert "text" in missing_text.output


def test_cap_json_is_bounded() -> None:
    text = _cap_json({"data": "x" * (MAX_RESULT_BYTES * 2)}, "read_state")
    assert len(text.encode("utf-8")) <= MAX_RESULT_BYTES
    payload = json.loads(text)
    assert payload["truncated"] is True
    assert payload["action"] == "read_state"
    assert payload["preview_json"]


def test_browser_effect_classification_and_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))

        def effect(action: str):
            return derive_effect(
                ToolCall(
                    id="call-1",
                    name="browser_bridge",
                    arguments={
                        "action": action,
                        "url": "https://example.com",
                    },
                ),
                workspace,
            )

        readonly = effect("read_state")
        assert isinstance(readonly, BrowserEffect)
        assert readonly.classification == "readonly"
        assert readonly.target == "https://example.com"
        assert approval_reason(readonly, ApprovalMode.MANUAL) is None
        assert plan_mode_denial(readonly) is None

        click = effect("click")
        assert click.classification == "mutating"
        assert approval_reason(click, ApprovalMode.MANUAL) == (
            "browser click"
        )
        assert approval_reason(click, ApprovalMode.ACCEPT_WRITE) == (
            "browser click"
        )
        assert approval_reason(click, ApprovalMode.AUTO) is None
        assert plan_mode_denial(click) is not None

        script = effect("execute_script")
        assert script.classification == "script"
        assert approval_reason(script, ApprovalMode.AUTO) == (
            "browser execute_script"
        )
        assert approval_reason(script, ApprovalMode.PLAN) == (
            "browser execute_script"
        )

        assert classify_browser_action("list_pages") == "readonly"
        assert classify_browser_action("type") == "mutating"


def test_plan_mode_hook_blocks_mutating_browser_action() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        hook = create_approval_hook(
            None,
            workspace,
            mode=ApprovalMode.PLAN,
        )
        call = ToolCall(
            id="call-1",
            name="browser_bridge",
            arguments={
                "action": "open_tab",
                "browser_id": "browser-1",
                "url": "https://example.com",
            },
        )
        decision = asyncio.run(
            hook(PreToolUse(call=call, context=ToolContext(step=1)))
        )
        assert decision is not None
        assert decision.allowed is False
        assert "Plan mode" in (decision.reason or "")


def test_prompt_and_tool_registration() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        prompt = build_system_prompt(
            workspace,
            tool_names=("browser_bridge", "read_file"),
        )
        assert "## Browser" in prompt.system_prompt
        assert "browser_bridge" in prompt.system_prompt
        assert 'load_skill(name="browser-bridge")' in prompt.system_prompt
        assert "browser" in prompt.sources

        without = build_system_prompt(workspace, tool_names=("read_file",))
        assert "## Browser" not in without.system_prompt
        assert "browser" not in without.sources


def test_coding_agent_registers_browser_tool() -> None:
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
            browser_bridge=FakeBridge(),
        )
        names = {
            tool.definition.name
            for tool in agent._create_tools(ApprovalMode.PLAN)
        }
        assert "browser_bridge" in names

        without = CodingAgent(
            model=Model(
                id="test",
                provider="test",
                api="openai-completions",
            ),
            stream_fn=stream,
            workspace_root=Path(temp),
        )
        assert "browser_bridge" not in {
            tool.definition.name
            for tool in without._create_tools(ApprovalMode.MANUAL)
        }


def test_browser_ui_entry() -> None:
    item = ToolTimelineItem(
        id="call-1",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=1,
        name="browser_bridge",
        arguments={
            "action": "open_tab",
            "browser_id": "browser-1",
            "url": "https://example.com",
        },
        status="completed",
        output='{"ok":true}',
    )
    entry = _tool_entry(item, waiting_for_approval=False)["tool"]
    assert entry["action"] == "Browser"
    assert entry["target"] == "open_tab https://example.com"
    assert entry["detail"]["kind"] == "io"

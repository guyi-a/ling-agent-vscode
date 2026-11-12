"""Tests for plan persistence and plan-mode tools."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from ling_agent import (
    ApprovalMode,
    CommittedEventEnvelope,
    ToolResultCommitted,
    ToolTimelineItem,
    Workspace,
    build_system_prompt,
)
from ling_agent.approvals import create_approval_hook
from ling_agent.plans import PlanStore
from ling_agent.tools.plan import create_plan_tools
from ling_ai.types import TextContent, ToolCall, ToolResultMessage
from ling_core import PreToolUse, ToolContext
from ling_rpc.ui_events import RpcEventSink, _tool_entry


def _tool(tools: list, name: str):
    return next(
        item for item in tools if item.definition.name == name
    )


def test_plan_store_creates_markdown_file() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = PlanStore(root)
        store.bind(session_id="session-1", run_id="run-1")

        path = store.create("# Fix login flow\n\n1. Read the code")
        assert path.exists()
        assert path.parent == root / ".agents" / "plans"
        assert "fix-login-flow" in path.name
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "status: ready" in text
        assert "session: session-1" in text
        assert "run: run-1" in text
        assert "# Fix login flow" in text


def test_plan_store_update_reuses_file() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = PlanStore(root)

        path = store.create("# First plan\n\nStep one")
        updated = store.update("# First plan\n\nStep two")

        assert updated == path
        files = list((root / ".agents" / "plans").iterdir())
        assert files == [path]
        text = path.read_text(encoding="utf-8")
        assert "Step two" in text
        assert "Step one" not in text


def test_plan_store_rejects_paths_outside_plans() -> None:
    with tempfile.TemporaryDirectory() as temp:
        store = PlanStore(Path(temp))
        with pytest.raises(ValueError):
            store.resolve("../outside.md")
        with pytest.raises(ValueError):
            store.resolve("notes.md")


def test_plan_tools_write_files_and_details() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = PlanStore(root)
        tools = create_plan_tools(store)
        create = _tool(tools, "create_plan")

        result = create.handler(
            ToolCall(
                id="call-1",
                name="create_plan",
                arguments={"plan": "# Ship it\n\n1. Do the thing"},
            ),
            ToolContext(step=1),
        )
        assert result.details["kind"] == "plan"
        assert result.details["path"].startswith(".agents/plans/")
        assert (root / result.details["path"]).is_file()

        update = _tool(tools, "update_plan")
        result = update.handler(
            ToolCall(
                id="call-2",
                name="update_plan",
                arguments={"plan": "# Ship it\n\n2. Do the other thing"},
            ),
            ToolContext(step=1),
        )
        assert result.details["kind"] == "plan"
        assert "other thing" in result.details["text"]


def test_todo_write_rejects_two_in_progress_items() -> None:
    with tempfile.TemporaryDirectory() as temp:
        tools = create_plan_tools(PlanStore(Path(temp)))
        todo = _tool(tools, "todo_write")
        with pytest.raises(ValueError):
            todo.handler(
                ToolCall(
                    id="call-1",
                    name="todo_write",
                    arguments={
                        "merge": True,
                        "todos": [
                            {
                                "id": "a",
                                "content": "A",
                                "status": "in_progress",
                            },
                            {
                                "id": "b",
                                "content": "B",
                                "status": "in_progress",
                            },
                        ],
                    },
                ),
                ToolContext(step=1),
            )


def test_todo_write_returns_structured_details() -> None:
    with tempfile.TemporaryDirectory() as temp:
        tools = create_plan_tools(PlanStore(Path(temp)))
        todo = _tool(tools, "todo_write")
        result = todo.handler(
            ToolCall(
                id="call-1",
                name="todo_write",
                arguments={
                    "merge": True,
                    "todos": [
                        {
                            "id": "1",
                            "content": "Read the code",
                            "status": "completed",
                        },
                        {
                            "id": "2",
                            "content": "Write the plan",
                            "status": "in_progress",
                        },
                    ],
                },
            ),
            ToolContext(step=1),
        )
        assert result.details["kind"] == "todo"
        assert result.details["todos"][1]["status"] == "in_progress"


def test_plan_mode_prompt_and_hook_deny_writes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = Workspace(root)
        prompt = build_system_prompt(
            workspace,
            tool_names=("read_file", "create_plan"),
            mode=ApprovalMode.PLAN,
        )
        assert "## Plan mode" in prompt.system_prompt
        assert "create_plan" in prompt.system_prompt

        hook = create_approval_hook(
            None,
            workspace,
            mode=ApprovalMode.PLAN,
        )
        write = ToolCall(
            id="call-1",
            name="write_file",
            arguments={"path": "a.txt", "content": "hello"},
        )
        decision = asyncio.run(
            hook(
                PreToolUse(
                    call=write,
                    context=ToolContext(step=1),
                )
            )
        )
        assert decision is not None
        assert decision.allowed is False
        assert "Plan mode" in (decision.reason or "")

        read = ToolCall(
            id="call-2",
            name="read_file",
            arguments={"path": "a.txt"},
        )
        assert (
            asyncio.run(
                hook(
                    PreToolUse(
                        call=read,
                        context=ToolContext(step=1),
                    )
                )
            )
            is None
        )


def test_tools_are_filtered_by_mode() -> None:
    from ling_agent import CodingAgent
    from ling_ai.types import Model

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
        )

        plan_names = {
            tool.definition.name
            for tool in agent._create_tools(ApprovalMode.PLAN)
        }
        assert "create_plan" in plan_names
        assert "update_plan" in plan_names
        assert "todo_write" in plan_names
        assert "spawn_subagent" in plan_names
        assert "write_file" not in plan_names
        assert "run_command" not in plan_names

        manual_names = {
            tool.definition.name
            for tool in agent._create_tools(ApprovalMode.MANUAL)
        }
        assert "create_plan" not in manual_names
        assert "write_file" in manual_names
        assert "todo_write" in manual_names
        assert "spawn_subagent" in manual_names


def test_plan_tool_detail_is_rendered() -> None:
    item = ToolTimelineItem(
        id="call-1",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=1,
        name="create_plan",
        arguments={"plan": "# Fix login\n\n1. Do it"},
        status="completed",
        output='{"path": ".agents/plans/x.md"}',
        details={
            "kind": "plan",
            "path": ".agents/plans/x.md",
            "text": "# Fix login\n\n1. Do it",
        },
    )
    entry = _tool_entry(item, waiting_for_approval=False)
    assert entry["tool"]["action"] == "Plan"
    assert entry["tool"]["detail"]["kind"] == "plan"
    assert entry["tool"]["detail"]["path"] == ".agents/plans/x.md"


def test_plan_created_event_is_emitted_once_per_create_plan() -> None:
    async def scenario() -> list[dict]:
        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        sink = RpcEventSink(emit)
        result = ToolResultMessage(
            tool_call_id="call-1",
            tool_name="create_plan",
            content=[TextContent('{"path": ".agents/plans/x.md"}')],
            is_error=False,
            timestamp=1,
            details={
                "kind": "plan",
                "path": ".agents/plans/x.md",
                "text": "# Fix login",
            },
        )
        envelope = CommittedEventEnvelope(
            session_id="session-1",
            seq=1,
            event_id="event-1",
            run_id="run-1",
            turn_id="turn-1",
            step=1,
            event=ToolResultCommitted(result=result),
        )
        await sink.emit_committed(envelope)
        return events

    events = asyncio.run(scenario())
    created = [event for event in events if event.get("type") == "plan.created"]
    assert created == [
        {
            "type": "plan.created",
            "path": ".agents/plans/x.md",
            "sessionId": "session-1",
        }
    ]

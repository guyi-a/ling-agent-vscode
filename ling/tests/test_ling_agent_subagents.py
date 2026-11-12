"""Tests for the read-only explore subagent."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from ling_agent import (
    ApprovalMode,
    CodingAgent,
    HarnessConfig,
    SubagentEffect,
    SubagentError,
    SubagentRunner,
    ToolTimelineItem,
    Workspace,
    build_system_prompt,
    derive_effect,
)
from ling_agent.approvals import create_approval_hook
from ling_agent.subagents import EXPLORE_TOOL_NAMES, build_explore_prompt
from ling_agent.tools.subagents import create_subagent_tools
from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    Usage,
)
from ling_core import PreToolUse, ToolContext


def _assistant(text: str, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)] if text else [],
        provider="test",
        model="test",
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
    )


def _tool_assistant(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> AssistantMessage:
    return AssistantMessage(
        content=[
            ToolCall(id=call_id, name=name, arguments=arguments)
        ],
        provider="test",
        model="test",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=1,
    )


class RecordingStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)
        self.contexts: list[Context] = []
        self.options: list[object] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        self.contexts.append(context)
        self.options.append(options)
        message = self.messages.pop(0)
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


def _runner(root: Path, helper: RecordingStreams, **config):
    workspace = Workspace(root)
    return SubagentRunner(
        model=Model(id="test", provider="test", api="openai-completions"),
        stream_fn=helper,
        workspace=workspace,
        config=HarnessConfig(**config),
    )


def test_explore_subagent_uses_read_only_tools() -> None:
    with tempfile.TemporaryDirectory() as temp:
        helper = RecordingStreams([_assistant("Found auth in src/auth.ts:12")])
        runner = _runner(Path(temp), helper)

        result = asyncio.run(
            runner.spawn_explore(
                "Find the auth middleware",
                "Locate where authentication is implemented.",
            )
        )

        assert result.summary == "Found auth in src/auth.ts:12"
        assert result.trace == (
            {
                "kind": "assistant",
                "id": "assistant-1",
                "text": "Found auth in src/auth.ts:12",
            },
        )
        context = helper.contexts[0]
        names = {tool.name for tool in context.tools}
        assert names == EXPLORE_TOOL_NAMES
        assert "write_file" not in names
        assert "run_command" not in names
        assert "spawn_subagent" not in names
        assert "explore subagent" in (context.system_prompt or "")
        assert "Find the auth middleware" in (context.system_prompt or "")
        assert context.messages[0].content == (
            "Locate where authentication is implemented."
        )


def test_explore_subagent_trace_records_tool_calls() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "a.txt").write_text("hello", encoding="utf-8")
        helper = RecordingStreams(
            [
                _tool_assistant(
                    "read-1",
                    "read_file",
                    {"path": "a.txt"},
                ),
                _assistant("The file says hello."),
            ]
        )
        emitted: list[tuple[str, dict]] = []
        runner = _runner(root, helper)
        runner.trace_sink = lambda call_id, entry: emitted.append(
            (call_id, entry)
        )

        result = asyncio.run(
            runner.spawn_explore(
                "Read a.txt",
                "Read a.txt and report its contents.",
                call_id="call-9",
            )
        )

        kinds = [entry["kind"] for entry in result.trace]
        assert kinds == ["tool_call", "tool_result", "assistant"]
        assert result.trace[0]["name"] == "read_file"
        assert "hello" in result.trace[1]["output"]
        assert result.trace[1]["isError"] is False
        assert result.trace[2]["text"] == "The file says hello."
        assert emitted == [("call-9", entry) for entry in result.trace]


def test_explore_subagent_truncates_long_summary() -> None:
    with tempfile.TemporaryDirectory() as temp:
        helper = RecordingStreams([_assistant("x" * 200)])
        runner = _runner(
            Path(temp),
            helper,
            subagent_max_summary_chars=50,
        )

        result = asyncio.run(runner.spawn_explore("task", "do it"))

        assert len(result.summary) < 200
        assert result.summary.endswith("…[truncated]")


def test_explore_subagent_errors() -> None:
    with tempfile.TemporaryDirectory() as temp:
        empty = RecordingStreams([_assistant("")])
        runner = _runner(Path(temp), empty)
        with pytest.raises(SubagentError, match="without a summary"):
            asyncio.run(runner.spawn_explore("task", "do it"))

        failed = RecordingStreams([_assistant("boom", stop_reason="error")])
        runner = _runner(Path(temp), failed)
        with pytest.raises(SubagentError):
            asyncio.run(runner.spawn_explore("task", "do it"))


def test_spawn_subagent_tool_returns_summary_and_details() -> None:
    with tempfile.TemporaryDirectory() as temp:
        helper = RecordingStreams([_assistant("The answer is 42.")])
        runner = _runner(Path(temp), helper)
        tool = create_subagent_tools(runner)[0]

        execution = asyncio.run(
            tool.handler(
                ToolCall(
                    id="call-1",
                    name="spawn_subagent",
                    arguments={
                        "description": "Answer the question",
                        "prompt": "Find the answer in the codebase.",
                    },
                ),
                ToolContext(step=1),
            )
        )

        payload = json.loads(execution.output)
        assert payload["mode"] == "foreground"
        assert payload["summary"] == "The answer is 42."
        assert execution.details["kind"] == "subagent"
        assert execution.details["summary"] == "The answer is 42."
        assert execution.details["trace"] == [
            {
                "kind": "assistant",
                "id": "assistant-1",
                "text": "The answer is 42.",
            }
        ]


def test_spawn_subagent_effect_and_plan_mode() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        call = ToolCall(
            id="call-1",
            name="spawn_subagent",
            arguments={
                "description": "Explore",
                "prompt": "Find the entry point.",
            },
        )
        effect = derive_effect(call, workspace)
        assert isinstance(effect, SubagentEffect)

        hook = create_approval_hook(
            None,
            workspace,
            mode=ApprovalMode.PLAN,
        )
        decision = asyncio.run(
            hook(PreToolUse(call=call, context=ToolContext(step=1)))
        )
        assert decision is None


def test_prompt_includes_subagent_policy() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        prompt = build_system_prompt(
            workspace,
            tool_names=("spawn_subagent", "read_file"),
        )

        assert "spawn_subagent" in prompt.system_prompt
        assert "## Subagents" in prompt.system_prompt
        assert "subagents" in prompt.sources

        without = build_system_prompt(workspace, tool_names=("read_file",))
        assert "## Subagents" not in without.system_prompt
        assert "subagents" not in without.sources


def test_explore_prompt_is_self_contained() -> None:
    text = build_explore_prompt("E:/repo", "Trace the login flow")
    assert "read-only" in text
    assert "E:/repo" in text
    assert "Trace the login flow" in text


def test_coding_agent_runs_spawn_subagent() -> None:
    with tempfile.TemporaryDirectory() as temp:
        helper = RecordingStreams(
            [
                AssistantMessage(
                    content=[
                        ToolCall(
                            id="call-1",
                            name="spawn_subagent",
                            arguments={
                                "description": "Find the entry point",
                                "prompt": "Find main() in the workspace.",
                            },
                        )
                    ],
                    provider="test",
                    model="test",
                    usage=Usage(),
                    stop_reason="tool_use",
                    timestamp=1,
                ),
                _assistant("main() is in app.py:3"),
                _assistant("The entry point is app.py:3."),
            ]
        )
        agent = CodingAgent(
            model=Model(
                id="test",
                provider="test",
                api="openai-completions",
            ),
            stream_fn=helper,
            workspace_root=Path(temp),
            config=HarnessConfig(subagent_max_steps=5),
        )

        async def run() -> str:
            try:
                result = await agent.prompt("Where is the entry point?")
                return "".join(
                    block.text
                    for block in result.content
                    if isinstance(block, TextContent)
                )
            finally:
                await agent.close()

        text = asyncio.run(run())

        assert text == "The entry point is app.py:3."
        assert len(helper.contexts) == 3
        child_context = helper.contexts[1]
        assert "explore subagent" in (child_context.system_prompt or "")
        assert "Find main() in the workspace." in (
            child_context.messages[0].content
        )


def test_spawn_subagent_ui_entry_without_summary_while_running() -> None:
    from ling_rpc.ui_events import _tool_entry

    item = ToolTimelineItem(
        id="call-1",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=1,
        name="spawn_subagent",
        arguments={"description": "Find auth"},
        status="running",
    )

    tool = _tool_entry(item, waiting_for_approval=False)["tool"]

    assert tool["action"] == "Subagent"
    assert tool["detail"]["kind"] == "subagent"
    assert "summary" not in tool["detail"]


def test_spawn_subagent_ui_entry() -> None:
    from ling_rpc.ui_events import _tool_entry

    item = ToolTimelineItem(
        id="call-1",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=1,
        name="spawn_subagent",
        arguments={"description": "Find auth"},
        status="completed",
        output=json.dumps(
            {
                "mode": "foreground",
                "subagentType": "explore",
                "description": "Find auth",
                "summary": "Auth lives in src/auth.ts.",
            }
        ),
        details={
            "kind": "subagent",
            "description": "Find auth",
            "subagentType": "explore",
            "mode": "foreground",
            "summary": "Auth lives in src/auth.ts.",
            "trace": [
                {
                    "kind": "tool_call",
                    "id": "read-1",
                    "name": "read_file",
                    "arguments": {"path": "src/auth.ts"},
                }
            ],
        },
    )

    entry = _tool_entry(item, waiting_for_approval=False)["tool"]

    assert entry["action"] == "Subagent"
    assert entry["target"] == "Find auth"
    assert entry["detail"]["kind"] == "subagent"
    assert entry["detail"]["summary"] == "Auth lives in src/auth.ts."
    assert entry["detail"]["trace"][0]["name"] == "read_file"


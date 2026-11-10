"""Tests for ling_agent command execution."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantContent,
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)
from ling_agent import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalRequest,
    HarnessConfig,
    Workspace,
    create_approval_hook,
    create_command_tools,
)
from ling_agent.tools.commands import run_command_process
from ling_core import Agent, HookRegistry, PreToolUse, ToolContext
from ling_core.tools import ToolRegistry

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _command(windows: str, posix: str) -> str:
    return windows if os.name == "nt" else posix


async def _command_process_contracts() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)

        success = await run_command_process(
            _command('Write-Output "hello"', "printf hello"),
            cwd=str(root),
            timeout_seconds=5,
            max_output_chars=1000,
        )
        assert success.exit_code == 0
        assert "hello" in success.stdout
        assert not success.timed_out
        assert not success.cancelled

        failed = await run_command_process(
            _command('Write-Error "boom"; exit 7', "echo boom >&2; exit 7"),
            cwd=str(root),
            timeout_seconds=5,
            max_output_chars=1000,
        )
        assert failed.exit_code == 7
        assert failed.to_tool_execution().is_error
        assert "boom" in (failed.stdout + failed.stderr)

        timed_out = await run_command_process(
            _command("Start-Sleep -Seconds 5", "sleep 5"),
            cwd=str(root),
            timeout_seconds=0.1,
            max_output_chars=1000,
        )
        assert timed_out.timed_out
        assert timed_out.to_tool_execution().is_error

        cancel = asyncio.Event()
        asyncio.get_running_loop().call_later(0.1, cancel.set)
        cancelled = await run_command_process(
            _command("Start-Sleep -Seconds 5", "sleep 5"),
            cwd=str(root),
            timeout_seconds=5,
            max_output_chars=1000,
            cancel_event=cancel,
        )
        assert cancelled.cancelled
        assert cancelled.to_tool_execution().is_error


def test_command_process_contracts() -> None:
    asyncio.run(_command_process_contracts())


async def _output_truncation() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = Workspace(
            root,
            HarnessConfig(max_command_output_chars=32),
        )
        registry = ToolRegistry(create_command_tools(workspace))
        result = await registry.execute(
            ToolCall(
                id="call_1",
                name="run_command",
                arguments={
                    "command": _command(
                        'Write-Output ("x" * 1000)',
                        "printf '%01000d' 0",
                    )
                },
            ),
            context=ToolContext(step=1),
        )

        assert not result.is_error
        assert "[truncated]" in result.content[0].text


def test_output_truncation() -> None:
    asyncio.run(_output_truncation())


class ApprovalHandler:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decision


def _assistant(
    content: list[AssistantContent],
    *,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
    )


class ScriptedStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        message = self.messages.pop(0)
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


def _run_approved_command(
    root: Path,
    decision: ApprovalDecision,
    command: str,
) -> tuple[AssistantMessage, list[ApprovalRequest], str]:
    workspace = Workspace(root)
    handler = ApprovalHandler(decision)
    hooks = HookRegistry()
    hooks.register(
        PreToolUse,
        create_approval_hook(handler, root, timeout_seconds=0.2),
    )
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(
                        id="call_1",
                        name="run_command",
                        arguments={"command": command},
                    )
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("done")]),
        ]
    )
    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        hooks=hooks,
        tools=create_command_tools(workspace),
    )
    result = asyncio.run(agent.run("run command"))
    tool_result = next(
        message
        for message in agent.context.messages
        if isinstance(message, ToolResultMessage)
    )
    return result, handler.requests, tool_result.content[0].text


def test_command_approval_denies_before_spawn() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        command = _command(
            "Set-Content -LiteralPath denied.txt -Value nope",
            "printf nope > denied.txt",
        )

        result, requests, output = _run_approved_command(
            root,
            ApprovalDecision.DENY,
            command,
        )

        assert result.content == [TextContent("done")]
        assert not (root / "denied.txt").exists()
        assert requests[0].kind is ApprovalKind.COMMAND
        assert "Permission denied" in output


def test_allowed_command_runs_in_workspace() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        command = _command(
            "Set-Content -LiteralPath allowed.txt -Value ok",
            "printf ok > allowed.txt",
        )

        result, requests, output = _run_approved_command(
            root,
            ApprovalDecision.ALLOW_ONCE,
            command,
        )

        assert result.content == [TextContent("done")]
        assert (root / "allowed.txt").read_text(encoding="utf-8").strip() == "ok"
        assert requests[0].kind is ApprovalKind.COMMAND
        assert "exit_code: 0" in output


if __name__ == "__main__":
    test_command_process_contracts()
    test_output_truncation()
    test_command_approval_denies_before_spawn()
    test_allowed_command_runs_in_workspace()
    print("all ling_agent command tests passed")

"""RPC-level tests for background subagent notifications and continuation."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ling_agent.effects import ApprovalMode
from ling_agent.events import BackgroundTasksCompleted
from ling_agent.subagents import SubagentError, SubagentResult
from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantContent,
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    Usage,
)
from ling_rpc.sessions import SessionService

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


class ScriptedStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)
        self.calls = 0

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        self.calls += 1
        if not self.messages:
            raise AssertionError("unexpected model call")
        message = self.messages.pop(0)
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


class FakeRunner:
    async def spawn_explore(
        self,
        description: str,
        prompt: str,
        *,
        call_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> SubagentResult:
        await asyncio.sleep(0.02)
        if cancel_event is not None and cancel_event.is_set():
            raise SubagentError("interrupted")
        return SubagentResult(
            summary=f"summary:{description}",
            trace=(),
        )


def _assistant(content: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(content)],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )


def _tool_assistant(call: ToolCall) -> AssistantMessage:
    return AssistantMessage(
        content=[call],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=1,
    )


def _command(seconds: float) -> str:
    if os.name == "nt":
        return (
            f"Start-Sleep -Milliseconds {int(seconds * 1000)}; "
            "Write-Output bgdone"
        )
    return f"sleep {seconds}; printf bgdone"


async def _emit(_event: dict[str, Any]) -> None:
    return None


async def _run(root: Path) -> None:
    workspace = root / "workspace"
    workspace.mkdir()
    streams = ScriptedStreams(
        [
            _assistant("main done"),
            _assistant("continued"),
        ]
    )
    service = SessionService(
        _emit,
        model=MODEL,
        stream_fn=streams,
        storage_root=root / "state",
    )
    try:
        await service.start("hello", str(workspace))
        await asyncio.wait_for(service.wait_for_idle(), timeout=3)
        session_id = service._session_id
        assert session_id is not None
        service._background_tasks._runner_factory = (
            lambda _workspace: FakeRunner()
        )

        for index in range(2):
            await service._background_tasks.submit(
                session_id=session_id,
                workspace_root=workspace,
                run_id="run-parent",
                tool_call_id=f"call-{index}",
                description=f"task {index}",
                prompt=f"prompt {index}",
            )

        snapshot = None
        for _ in range(200):
            snapshot = await service.snapshot()
            notices = [
                entry
                for entry in snapshot["entries"]
                if entry.get("kind") == "task-notification"
            ]
            assistant_texts = [
                entry["message"]["content"]
                for entry in snapshot["entries"]
                if entry.get("kind") == "message"
                and entry["message"]["role"] == "assistant"
            ]
            if notices and "continued" in assistant_texts:
                break
            await asyncio.sleep(0.02)

        assert snapshot is not None
        notices = [
            entry
            for entry in snapshot["entries"]
            if entry.get("kind") == "task-notification"
        ]
        assert len(notices) == 1
        assert len(notices[0]["tasks"]) == 2
        assistant_texts = [
            entry["message"]["content"]
            for entry in snapshot["entries"]
            if entry.get("kind") == "message"
            and entry["message"]["role"] == "assistant"
        ]
        assert assistant_texts[-1] == "continued"
        assert len(
            [text for text in assistant_texts if text == "continued"]
        ) == 1

        events = await service._history_store.load_committed(session_id)
        completed = [
            envelope.event
            for envelope in events
            if isinstance(envelope.event, BackgroundTasksCompleted)
        ]
        assert len(completed) == 1
        assert len(completed[0].tasks) == 2

        tasks = await service.background_list()
        assert len(tasks) == 2
        assert all(task["status"] == "completed" for task in tasks)
    finally:
        await service.close()


async def _run_command_detach(root: Path) -> None:
    workspace = root / "workspace"
    workspace.mkdir()
    command = _command(1.5)
    streams = ScriptedStreams(
        [
            _tool_assistant(
                ToolCall(
                    id="call-cmd",
                    name="run_command",
                    arguments={"command": command},
                )
            ),
            _assistant("started"),
            _assistant("continued"),
        ]
    )
    service = SessionService(
        _emit,
        model=MODEL,
        stream_fn=streams,
        storage_root=root / "state",
    )
    try:
        await service.start(
            "run it",
            str(workspace),
            approval_mode=ApprovalMode.AUTO,
        )
        tool_entry = None
        for _ in range(300):
            snapshot = await service.snapshot()
            tool_entry = next(
                (
                    entry
                    for entry in snapshot["entries"]
                    if entry.get("kind") == "tool"
                    and entry["tool"]["id"] == "call-cmd"
                ),
                None,
            )
            if (
                tool_entry is not None
                and tool_entry["tool"]["status"] == "Running"
            ):
                break
            await asyncio.sleep(0.02)
        assert tool_entry is not None
        assert tool_entry["tool"]["status"] == "Running"

        view = await service.background_tool_call("call-cmd")
        assert view["kind"] == "command"
        assert view["status"] == "running"
        assert view["outputPath"]

        detail = None
        for _ in range(300):
            snapshot = await service.snapshot()
            tool_entry = next(
                (
                    entry
                    for entry in snapshot["entries"]
                    if entry.get("kind") == "tool"
                    and entry["tool"]["id"] == "call-cmd"
                ),
                None,
            )
            detail = (
                tool_entry["tool"].get("detail")
                if tool_entry is not None
                else None
            )
            if isinstance(detail, dict) and detail.get("taskId"):
                break
            await asyncio.sleep(0.02)
        assert isinstance(detail, dict)
        assert detail["mode"] == "background"
        assert detail["taskId"] == view["id"]
        assert detail["logPath"]
        assert view["id"] in detail["logPath"]

        notices = []
        texts: list[str] = []
        for _ in range(400):
            snapshot = await service.snapshot()
            notices = [
                entry
                for entry in snapshot["entries"]
                if entry.get("kind") == "task-notification"
            ]
            texts = [
                entry["message"]["content"]
                for entry in snapshot["entries"]
                if entry.get("kind") == "message"
                and entry["message"]["role"] == "assistant"
            ]
            if notices and "continued" in texts:
                break
            await asyncio.sleep(0.02)
        assert len(notices) == 1
        notice = notices[0]["tasks"][0]
        assert notice["kind"] == "command"
        assert notice["exitCode"] == 0

        tasks = await service.background_list()
        assert len(tasks) == 1
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["exitCode"] == 0
        assert "bgdone" in (tasks[0]["summary"] or "")
        assert Path(tasks[0]["outputPath"]).exists()
    finally:
        await service.close()


class BackgroundTasksRpcTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_tasks_batch_into_one_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            await _run(Path(temp))

    async def test_running_command_can_be_detached(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            await _run_command_detach(Path(temp))


if __name__ == "__main__":
    unittest.main()

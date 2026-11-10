"""End-to-end tests for the public CodingAgent facade."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from ling_ai.events import (
    DoneEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
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
from ling_agent import (
    ApprovalDecision,
    ApprovalRequest,
    CallbackEventSink,
    CodingAgent,
    SQLiteSessionStore,
)

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _command(windows: str, posix: str) -> str:
    return windows if os.name == "nt" else posix


class AllowApprovalHandler:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision.ALLOW_ONCE


class BlockingApprovalHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _assistant(
    content: list[AssistantContent],
    *,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(input_tokens=2, output_tokens=2, total_tokens=4),
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
        for content_index, block in enumerate(message.content):
            if isinstance(block, TextContent) and block.text:
                stream.push(
                    TextStartEvent(content_index=content_index, partial=message)
                )
                stream.push(
                    TextDeltaEvent(
                        content_index=content_index,
                        delta=block.text,
                        partial=message,
                    )
                )
                stream.push(
                    TextEndEvent(
                        content_index=content_index,
                        content=block.text,
                        partial=message,
                    )
                )
            elif isinstance(block, ToolCall):
                stream.push(
                    ToolCallStartEvent(content_index=content_index, partial=message)
                )
                stream.push(
                    ToolCallDeltaEvent(
                        content_index=content_index,
                        delta=json.dumps(block.arguments),
                        partial=message,
                    )
                )
                stream.push(
                    ToolCallEndEvent(
                        content_index=content_index,
                        tool_call=block,
                        partial=message,
                    )
                )
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


async def _integration(root: Path) -> None:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("old\n", encoding="utf-8")
    state = root / "state"
    approvals = AllowApprovalHandler()
    committed = []
    frames = []
    sink = CallbackEventSink(
        on_committed=committed.append,
        on_stream_frame=frames.append,
    )
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(
                        id="read-1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _assistant(
                [
                    ToolCall(
                        id="edit-1",
                        name="edit_file",
                        arguments={
                            "path": "README.md",
                            "old_text": "old",
                            "new_text": "new",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _assistant(
                [
                    ToolCall(
                        id="verify-1",
                        name="run_command",
                        arguments={
                            "command": _command(
                                "Get-Content -LiteralPath README.md",
                                "cat README.md",
                            )
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("completed")]),
        ]
    )
    coding = CodingAgent(
        model=MODEL,
        stream_fn=streams,
        workspace_root=workspace,
        storage_root=state,
        approvals=approvals,
        event_sink=sink,
    )
    try:
        result = await coding.prompt(
            "Inspect README.md, replace old with new, then verify the file."
        )
        session = coding.session
        assert session is not None
        snapshot = await coding.timeline_snapshot()
        events = await coding.store.load_committed(session.session_id)
    finally:
        await coding.close()

    assert result.content == [TextContent("completed")]
    assert (workspace / "README.md").read_text(encoding="utf-8").strip() == "new"
    assert [request.tool_name for request in approvals.requests] == [
        "edit_file",
    ]
    assert committed
    assert frames
    assert snapshot is not None
    tools = [item for item in snapshot.items if item.kind == "tool"]
    approvals_items = [
        item for item in snapshot.items if item.kind == "approval"
    ]
    assert [item.status for item in tools] == [
        "completed",
        "completed",
        "completed",
    ]
    assert [item.status for item in approvals_items] == [
        "approved",
    ]
    assert snapshot.runs[events[0].run_id].status == "completed"
    command_results = [
        envelope.event.result
        for envelope in events
        if type(envelope.event).__name__ == "ToolResultCommitted"
        and envelope.event.result.tool_name == "run_command"
    ]
    assert command_results
    assert "new" in command_results[0].content[0].text


async def _close_pending_approval(root: Path) -> None:
    workspace = root / "workspace"
    workspace.mkdir()
    state = root / "state"
    approval = BlockingApprovalHandler()
    coding = CodingAgent(
        model=MODEL,
        stream_fn=ScriptedStreams(
            [
                _assistant(
                    [
                        ToolCall(
                            id="write-1",
                            name="write_file",
                            arguments={"path": "blocked.txt", "content": "no"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                _assistant([TextContent("unexpected completion")]),
            ]
        ),
        workspace_root=workspace,
        storage_root=state,
        approvals=approval,
    )
    prompt_task = asyncio.create_task(coding.prompt("write blocked.txt"))
    await asyncio.wait_for(approval.started.wait(), timeout=1)
    session = coding.session
    assert session is not None
    session_id = session.session_id

    close_task = asyncio.create_task(coding.close())
    result = await asyncio.wait_for(prompt_task, timeout=2)
    await asyncio.wait_for(close_task, timeout=2)

    assert result.stop_reason == "aborted"
    assert not (workspace / "blocked.txt").exists()

    store = SQLiteSessionStore(state / "sessions.db")
    try:
        events = await store.load_committed(session_id)
    finally:
        await store.close()
    resolved = [
        envelope.event
        for envelope in events
        if type(envelope.event).__name__ == "ApprovalResolved"
    ]
    tool_results = [
        envelope.event
        for envelope in events
        if type(envelope.event).__name__ == "ToolResultCommitted"
    ]
    assert resolved[-1].decision is ApprovalDecision.DENY
    assert tool_results[-1].result.is_error
    assert "interrupted" in tool_results[-1].result.content[0].text


def test_coding_agent_inspect_edit_verify() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_integration(Path(temp)))


def test_close_rejects_pending_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_close_pending_approval(Path(temp)))


if __name__ == "__main__":
    test_coding_agent_inspect_edit_verify()
    test_close_rejects_pending_approval()
    print("all ling_agent integration tests passed")

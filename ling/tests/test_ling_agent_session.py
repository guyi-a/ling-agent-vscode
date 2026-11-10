"""End-to-end tests for Session commit boundaries."""

from __future__ import annotations

import asyncio
import json
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
    UserMessage,
)
from ling_agent import (
    ApprovalDecision,
    ApprovalRequest,
    CallbackEventSink,
    Session,
    SessionApprovalHandler,
    SessionPersistenceError,
    SQLiteSessionStore,
    StreamFrameBuffer,
    TimelineProjector,
    Workspace,
    create_approval_hook,
    create_mutation_tools,
)
from ling_core import Agent, HookRegistry, PreToolUse

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


class StaticApprovalHandler:
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
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
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


def _hook_registry(session: Session, approval_handler: StaticApprovalHandler) -> HookRegistry:
    hooks = HookRegistry()
    hooks.register(
        PreToolUse,
        create_approval_hook(
            SessionApprovalHandler(session, approval_handler),
            session.record.workspace_root,
            timeout_seconds=1,
        ),
    )
    session.register_hooks(hooks)
    return hooks


async def _allowed_tool_flow(root: Path) -> None:
    database = root / "sessions.db"
    workspace = Workspace(root)
    store = SQLiteSessionStore(database)
    committed = []
    streamed = []
    sink = CallbackEventSink(
        on_committed=committed.append,
        on_stream_frame=streamed.append,
    )
    session = await Session.create(
        store,
        workspace_root=root,
        title="integration",
        context=Context(),
        sink=sink,
        frames=StreamFrameBuffer(),
    )
    await session.start_run(UserMessage(content="write a file", timestamp=1))
    approval_handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)
    hooks = _hook_registry(session, approval_handler)
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    TextContent("working"),
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "a.txt", "content": "hello"},
                    ),
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("done")]),
        ]
    )
    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        context=session.context,
        hooks=hooks,
        tools=create_mutation_tools(workspace),
        max_steps=4,
    )

    result = await agent.run(None, on_event=session.handle_model_event)
    await session.stop_run("completed", result)

    assert (root / "a.txt").read_text(encoding="utf-8") == "hello"
    assert len(streamed) >= 3
    events = await store.load_committed(session.session_id)
    kinds = [type(envelope.event).__name__ for envelope in events]
    assert kinds == [
        "RunStarted",
        "UserMessageCommitted",
        "AssistantMessageCommitted",
        "ApprovalRequested",
        "ApprovalResolved",
        "ToolExecutionStarted",
        "ToolResultCommitted",
        "AssistantMessageCommitted",
        "RunStopped",
    ]
    assert approval_handler.requests[0].call_id == "call-1"

    projector = TimelineProjector(session.session_id)
    for envelope in events:
        projector.push_committed(envelope)
    snapshot = projector.snapshot()
    tools = [item for item in snapshot.items if item.kind == "tool"]
    approvals = [item for item in snapshot.items if item.kind == "approval"]
    assert tools[0].status == "completed"
    assert approvals[0].status == "approved"
    assert snapshot.runs[session.run_id or events[0].run_id].status == "completed"

    await store.close()


async def _denied_tool_flow(root: Path) -> None:
    database = root / "sessions.db"
    workspace = Workspace(root)
    store = SQLiteSessionStore(database)
    session = await Session.create(
        store,
        workspace_root=root,
        context=Context(),
    )
    await session.start_run(UserMessage(content="write denied", timestamp=1))
    approval_handler = StaticApprovalHandler(ApprovalDecision.DENY)
    hooks = _hook_registry(session, approval_handler)
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(
                        id="call-1",
                        name="write_file",
                        arguments={"path": "denied.txt", "content": "no"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("recovered")]),
        ]
    )
    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        context=session.context,
        hooks=hooks,
        tools=create_mutation_tools(workspace),
        max_steps=4,
    )

    result = await agent.run(None, on_event=session.handle_model_event)
    await session.stop_run("completed", result)

    assert not (root / "denied.txt").exists()
    events = await store.load_committed(session.session_id)
    kinds = [type(envelope.event).__name__ for envelope in events]
    assert "ApprovalRequested" in kinds
    assert "ApprovalResolved" in kinds
    assert "ToolResultCommitted" in kinds
    assert "ToolExecutionStarted" not in kinds
    denied_result = next(
        envelope
        for envelope in events
        if type(envelope.event).__name__ == "ToolResultCommitted"
    )
    assert denied_result.event.result.is_error

    await store.close()


async def _persistence_failure(root: Path) -> None:
    store = SQLiteSessionStore(root / "sessions.db")
    session = await Session.create(
        store,
        workspace_root=root,
        context=Context(),
    )
    await store.close()

    try:
        await session.start_run(UserMessage(content="hello", timestamp=1))
    except SessionPersistenceError:
        pass
    else:
        raise AssertionError("Expected SessionPersistenceError")


def test_session_commit_boundaries_and_timeline() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_allowed_tool_flow(Path(temp)))


def test_denied_approval_is_persisted_before_recovery() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_denied_tool_flow(Path(temp)))


def test_session_persistence_failure_is_fatal() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_persistence_failure(Path(temp)))


if __name__ == "__main__":
    test_session_commit_boundaries_and_timeline()
    test_denied_approval_is_persisted_before_recovery()
    test_session_persistence_failure_is_fatal()
    print("all ling_agent session tests passed")

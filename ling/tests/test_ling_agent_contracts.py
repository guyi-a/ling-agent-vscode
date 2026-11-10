"""Tests for ling_agent approval and event contracts."""

from __future__ import annotations

from pathlib import Path

from ling_ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ling_agent import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalRequest,
    ApprovalRequested,
    ApprovalResolved,
    AssistantMessageCommitted,
    AssistantTextDelta,
    CommittedEventEnvelope,
    HarnessConfig,
    RunStarted,
    RunStopped,
    StreamFrameEnvelope,
    ToolExecutionStarted,
    ToolResultCommitted,
    UserMessageCommitted,
)


def test_config_validation() -> None:
    config = HarnessConfig()
    assert config.max_read_lines == 400

    try:
        HarnessConfig(max_read_lines=0)
    except ValueError as exc:
        assert "max_read_lines" in str(exc)
    else:
        raise AssertionError("Expected invalid config rejection")


def test_approval_and_event_contracts() -> None:
    request = ApprovalRequest(
        kind=ApprovalKind.WRITE,
        tool_name="write_file",
        arguments={"path": "a.txt", "content": "hello"},
        reason="Write requires approval",
        workspace_root=Path("E:/workspace"),
    )
    call = ToolCall(id="call_1", name="write_file", arguments=request.arguments)
    result = ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=[TextContent("written")],
        is_error=False,
        timestamp=1,
    )
    user = UserMessage(content="hello", timestamp=1)
    assistant = AssistantMessage(
        content=[TextContent("working"), call],
        provider="test",
        model="test",
        usage=Usage(),
        stop_reason="tool_use",
        timestamp=2,
    )

    run_started = CommittedEventEnvelope(
        session_id="session-1",
        seq=1,
        event_id="event-1",
        run_id="run-1",
        turn_id="turn-1",
        step=0,
        event=RunStarted(),
    )
    user_committed = CommittedEventEnvelope(
        session_id="session-1",
        seq=2,
        event_id="event-2",
        run_id="run-1",
        turn_id="turn-1",
        step=0,
        message_id="message-user-1",
        event=UserMessageCommitted(message=user),
    )
    assistant_committed = CommittedEventEnvelope(
        session_id="session-1",
        seq=3,
        event_id="event-3",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        message_id="message-assistant-1",
        event=AssistantMessageCommitted(message=assistant),
    )
    approval = CommittedEventEnvelope(
        session_id="session-1",
        seq=4,
        event_id="event-4",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        call_id=call.id,
        approval_id="approval-1",
        event=ApprovalRequested(approval_id="approval-1", request=request),
    )
    approval_resolved = CommittedEventEnvelope(
        session_id="session-1",
        seq=5,
        event_id="event-5",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        call_id=call.id,
        approval_id="approval-1",
        event=ApprovalResolved(
            approval_id="approval-1",
            request=request,
            decision=ApprovalDecision.ALLOW_ONCE,
        ),
    )
    tool_started = CommittedEventEnvelope(
        session_id="session-1",
        seq=6,
        event_id="event-6",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        call_id=call.id,
        event=ToolExecutionStarted(call=call),
    )
    tool_finished = CommittedEventEnvelope(
        session_id="session-1",
        seq=7,
        event_id="event-7",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        call_id=call.id,
        event=ToolResultCommitted(result=result),
    )
    stopped = CommittedEventEnvelope(
        session_id="session-1",
        seq=8,
        event_id="event-8",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        event=RunStopped(status="completed", message=assistant),
    )
    frame = StreamFrameEnvelope(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        frame_seq=1,
        message_id="message-assistant-1",
        frame=AssistantTextDelta(delta="work"),
    )

    events = [
        run_started,
        user_committed,
        assistant_committed,
        approval,
        approval_resolved,
        tool_started,
        tool_finished,
        stopped,
    ]
    assert [envelope.seq for envelope in events] == list(range(1, 9))
    assert approval.approval_id == approval_resolved.approval_id
    assert tool_started.call_id == tool_finished.call_id == call.id
    assert frame.frame_seq == 1
    assert frame.message_id == "message-assistant-1"
    assert ApprovalDecision.ALLOW_ONCE.value == "allow_once"

    try:
        StreamFrameEnvelope(
            session_id="session-1",
            run_id="run-1",
            turn_id="turn-1",
            step=1,
            frame_seq=0,
            frame=AssistantTextDelta(delta="bad"),
        )
    except ValueError as exc:
        assert "frame_seq" in str(exc)
    else:
        raise AssertionError("Expected invalid frame sequence rejection")


if __name__ == "__main__":
    test_config_validation()
    test_approval_and_event_contracts()
    print("all ling_agent contract tests passed")

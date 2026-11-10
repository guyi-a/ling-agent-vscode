"""Tests for committed-event and stream-frame timeline projection."""

from __future__ import annotations

from pathlib import Path

from ling_ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
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
    AssistantThinkingDelta,
    CompactionApplied,
    CompactionTimelineItem,
    CommittedEventEnvelope,
    RunStarted,
    RunStopped,
    StreamFrameEnvelope,
    TimelineProjector,
    ToolArgumentsDelta,
    ToolExecutionStarted,
    ToolResultCommitted,
    UserMessageCommitted,
)


def _envelope(
    seq: int,
    event: object,
    *,
    message_id: str | None = None,
    call_id: str | None = None,
    approval_id: str | None = None,
) -> CommittedEventEnvelope:
    return CommittedEventEnvelope(
        session_id="session-1",
        seq=seq,
        event_id=f"event-{seq}",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        event=event,
        emitted_at=seq,
        message_id=message_id,
        call_id=call_id,
        approval_id=approval_id,
    )


def _frame(
    frame_seq: int,
    frame: object,
    *,
    message_id: str | None = None,
    call_id: str | None = None,
) -> StreamFrameEnvelope:
    return StreamFrameEnvelope(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        frame_seq=frame_seq,
        frame=frame,
        message_id=message_id,
        call_id=call_id,
    )


def test_timeline_projector_compaction_marker() -> None:
    from ling_rpc.ui_events import snapshot_to_ui

    projector = TimelineProjector("session-1")
    projector.push_committed(
        _envelope(
            1,
            CompactionApplied(
                compaction_id="compaction-1",
                through_message_id="assistant:4",
                summary=UserMessage(content="summary", timestamp=1),
                replaced_message_ids=("user:0", "assistant:1"),
                replaced_count=2,
                estimated_tokens=123,
            ),
        )
    )

    snapshot = projector.snapshot()

    assert len(snapshot.items) == 1
    item = snapshot.items[0]
    assert isinstance(item, CompactionTimelineItem)
    assert item.compaction_id == "compaction-1"
    assert item.replaced_count == 2

    ui = snapshot_to_ui(snapshot)
    compaction_entries = [
        entry
        for entry in ui["entries"]
        if entry.get("kind") == "compaction"
    ]
    assert len(compaction_entries) == 1
    assert (
        compaction_entries[0]["compaction"]["replacedCount"] == 2
    )


def test_timeline_projector_reconciles_stream_and_commit() -> None:
    projector = TimelineProjector("session-1")
    call = ToolCall(id="call-1", name="read_file", arguments={"path": "a.txt"})
    request = ApprovalRequest(
        kind=ApprovalKind.WRITE,
        tool_name="read_file",
        arguments=call.arguments,
        reason="test",
        workspace_root=Path("E:/workspace"),
    )
    final_message = AssistantMessage(
        content=[
            ThinkingContent("reasoning"),
            TextContent("canonical answer"),
            call,
        ],
        provider="test",
        model="test-model",
        usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
        stop_reason="tool_use",
        timestamp=3,
    )

    projector.push_committed(_envelope(1, RunStarted()))
    projector.push_committed(
        _envelope(
            2,
            UserMessageCommitted(
                message=UserMessage(content="read it", timestamp=1)
            ),
            message_id="user-message",
        )
    )
    projector.push_frame(
        _frame(
            1,
            AssistantThinkingDelta(delta="rea"),
            message_id="assistant-message",
        )
    )
    projector.push_frame(
        _frame(
            2,
            AssistantTextDelta(delta="partial"),
            message_id="assistant-message",
        )
    )
    projector.push_frame(
        _frame(
            3,
            ToolArgumentsDelta(delta='{"path":'),
            call_id=call.id,
        )
    )
    projector.push_committed(
        _envelope(
            3,
            AssistantMessageCommitted(message=final_message),
            message_id="assistant-message",
        )
    )
    projector.push_frame(
        _frame(
            4,
            AssistantTextDelta(delta=" late"),
            message_id="assistant-message",
        )
    )
    projector.push_committed(
        _envelope(
            4,
            ApprovalRequested(approval_id="approval-1", request=request),
            call_id=call.id,
            approval_id="approval-1",
        )
    )
    projector.push_committed(
        _envelope(
            5,
            ApprovalResolved(
                approval_id="approval-1",
                request=request,
                decision=ApprovalDecision.ALLOW_ONCE,
            ),
            call_id=call.id,
            approval_id="approval-1",
        )
    )
    projector.push_committed(
        _envelope(
            6,
            ToolExecutionStarted(call=call),
            call_id=call.id,
        )
    )
    projector.push_committed(
        _envelope(
            7,
            ToolResultCommitted(
                result=ToolResultMessage(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=[TextContent("file contents")],
                    is_error=False,
                    timestamp=4,
                )
            ),
            call_id=call.id,
        )
    )
    projector.push_committed(
        _envelope(
            8,
            RunStopped(status="completed", message=final_message),
            message_id="assistant-message",
        )
    )

    snapshot = projector.snapshot()
    assert snapshot.last_seq == 8
    assert snapshot.frame_seq_by_run == {"run-1": 4}
    assistant_items = [
        item for item in snapshot.items if item.kind == "assistant"
    ]
    assert len(assistant_items) == 1
    assistant = assistant_items[0]
    assert assistant.text == "canonical answer"
    assert assistant.thinking == "reasoning"
    assert assistant.status == "completed"
    tool_items = [item for item in snapshot.items if item.kind == "tool"]
    assert len(tool_items) == 1
    assert tool_items[0].status == "completed"
    assert tool_items[0].output == "file contents"
    approval_items = [
        item for item in snapshot.items if item.kind == "approval"
    ]
    assert approval_items[0].status == "approved"
    assert snapshot.runs["run-1"].status == "completed"


def test_timeline_projector_ignores_duplicate_sequences() -> None:
    projector = TimelineProjector("session-1")
    projector.push_committed(_envelope(1, RunStarted()))
    first = _frame(
        1,
        AssistantTextDelta(delta="once"),
        message_id="assistant-message",
    )
    projector.push_frame(first)
    projector.push_frame(first)
    projector.push_committed(
        _envelope(
            1,
            UserMessageCommitted(
                message=UserMessage(content="duplicate", timestamp=1)
            ),
            message_id="user-message",
        )
    )

    snapshot = projector.snapshot()
    assistants = [item for item in snapshot.items if item.kind == "assistant"]
    users = [item for item in snapshot.items if item.kind == "user"]
    assert assistants[0].text == "once"
    assert users == []


if __name__ == "__main__":
    test_timeline_projector_reconciles_stream_and_commit()
    test_timeline_projector_ignores_duplicate_sequences()
    print("all ling_agent timeline tests passed")

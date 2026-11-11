"""Project committed events and live frames into frontend timeline items."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, TypeAlias

from ling_ai.types import AssistantMessage, TextContent, ThinkingContent, ToolCall, Usage

from .approvals import ApprovalRequest
from .attachments import UserAttachment
from .selections import UserSelection
from .events import (
    ApprovalRequested,
    ApprovalResolved,
    AssistantMessageCommitted,
    AssistantTextDelta,
    AssistantThinkingDelta,
    AssistantThinkingDone,
    CompactionApplied,
    CommittedEventEnvelope,
    RunStarted,
    RunStopped,
    StreamFrameEnvelope,
    ToolArgumentsDelta,
    ToolExecutionStarted,
    ToolResultCommitted,
    ToolStatusChanged,
    UsageUpdated,
    UserMessageCommitted,
)


@dataclass(slots=True)
class UserTimelineItem:
    id: str
    run_id: str
    created_seq: int
    text: str
    attachments: tuple[UserAttachment, ...] = ()
    selections: tuple[UserSelection, ...] = ()
    kind: Literal["user"] = "user"


@dataclass(slots=True)
class AssistantTimelineItem:
    id: str
    run_id: str
    turn_id: str
    created_seq: int
    text: str = ""
    thinking: str = ""
    thinking_done: bool = False
    status: Literal["streaming", "completed", "error", "aborted"] = "streaming"
    stop_reason: str | None = None
    usage: Usage | None = None
    error: str | None = None
    kind: Literal["assistant"] = "assistant"


@dataclass(slots=True)
class ToolTimelineItem:
    id: str
    run_id: str
    turn_id: str
    created_seq: int
    name: str
    arguments: dict[str, object]
    arguments_text: str = ""
    status: Literal[
        "requested",
        "running",
        "completed",
        "failed",
        "cancelled",
    ] = "requested"
    output: str = ""
    error: str | None = None
    kind: Literal["tool"] = "tool"


@dataclass(slots=True)
class ApprovalTimelineItem:
    id: str
    run_id: str
    turn_id: str
    created_seq: int
    call_id: str | None
    request: ApprovalRequest
    status: Literal["pending", "approved", "denied"] = "pending"
    kind: Literal["approval"] = "approval"


@dataclass(slots=True)
class CompactionTimelineItem:
    id: str
    run_id: str
    created_seq: int
    compaction_id: str
    through_message_id: str
    replaced_count: int
    kind: Literal["compaction"] = "compaction"


@dataclass(slots=True)
class TimelineRun:
    id: str
    status: Literal["running", "completed", "cancelled", "error"] = "running"


TimelineItem: TypeAlias = (
    UserTimelineItem
    | AssistantTimelineItem
    | ToolTimelineItem
    | ApprovalTimelineItem
    | CompactionTimelineItem
)


@dataclass(slots=True)
class TimelineSnapshot:
    session_id: str
    last_seq: int
    items: list[TimelineItem]
    runs: dict[str, TimelineRun]
    frame_seq_by_run: dict[str, int]


def _text_from_message(message: AssistantMessage) -> tuple[str, str]:
    text = "".join(
        block.text for block in message.content if isinstance(block, TextContent)
    )
    thinking = "".join(
        block.thinking
        for block in message.content
        if isinstance(block, ThinkingContent)
    )
    return text, thinking


def _assistant_status(
    message: AssistantMessage,
) -> Literal["completed", "error", "aborted"]:
    if message.stop_reason == "aborted":
        return "aborted"
    if message.stop_reason == "error":
        return "error"
    return "completed"


class TimelineProjector:
    """Fold committed events and live frames into stable UI items."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._last_seq = 0
        self._items: list[TimelineItem] = []
        self._runs: dict[str, TimelineRun] = {}
        self._frame_seq_by_run: dict[str, int] = {}
        self._committed_assistant_ids: set[str] = set()
        self._completed_tool_ids: set[str] = set()
        self._local_seq = 0

    def push_committed(self, envelope: CommittedEventEnvelope) -> None:
        if envelope.session_id != self.session_id:
            raise ValueError("Committed event belongs to another session")
        if envelope.seq <= self._last_seq:
            return
        self._last_seq = envelope.seq
        event = envelope.event

        if isinstance(event, RunStarted):
            self._runs[envelope.run_id] = TimelineRun(id=envelope.run_id)
            return

        if isinstance(event, UserMessageCommitted):
            item_id = envelope.message_id or envelope.event_id
            self._upsert(
                UserTimelineItem(
                    id=item_id,
                    run_id=envelope.run_id,
                    created_seq=envelope.seq,
                    text=_user_display_text(event),
                    attachments=tuple(event.attachments),
                    selections=tuple(event.selections),
                )
            )
            return

        if isinstance(event, AssistantMessageCommitted):
            item_id = envelope.message_id or (
                f"{envelope.run_id}:{envelope.turn_id}:assistant"
            )
            text, thinking = _text_from_message(event.message)
            existing = self._find(item_id)
            if isinstance(existing, AssistantTimelineItem):
                existing.text = text
                existing.thinking = thinking
                existing.thinking_done = True
                existing.status = _assistant_status(event.message)
                existing.stop_reason = event.message.stop_reason
                existing.usage = event.message.usage
                existing.error = event.message.error_message
            else:
                self._items.append(
                    AssistantTimelineItem(
                        id=item_id,
                        run_id=envelope.run_id,
                        turn_id=envelope.turn_id,
                        created_seq=envelope.seq,
                        text=text,
                        thinking=thinking,
                        thinking_done=True,
                        status=_assistant_status(event.message),
                        stop_reason=event.message.stop_reason,
                        usage=event.message.usage,
                        error=event.message.error_message,
                    )
                )
            self._committed_assistant_ids.add(item_id)
            for call in event.message.content:
                if isinstance(call, ToolCall):
                    self._upsert_tool(
                        envelope,
                        call,
                        status="requested",
                    )
            return

        if isinstance(event, ToolExecutionStarted):
            self._upsert_tool(
                envelope,
                event.call,
                status="running",
            )
            return

        if isinstance(event, ToolResultCommitted):
            item = self._find(event.result.tool_call_id)
            status = "failed" if event.result.is_error else "completed"
            output = _result_text(event.result)
            if isinstance(item, ToolTimelineItem):
                item.status = status
                item.output = output
                item.error = output if event.result.is_error else None
            else:
                item = ToolTimelineItem(
                    id=event.result.tool_call_id,
                    run_id=envelope.run_id,
                    turn_id=envelope.turn_id,
                    created_seq=envelope.seq,
                    name=event.result.tool_name,
                    arguments={},
                    status=status,
                    output=output,
                    error=output if event.result.is_error else None,
                )
                self._items.append(item)
            self._completed_tool_ids.add(event.result.tool_call_id)
            return

        if isinstance(event, ApprovalRequested):
            self._upsert(
                ApprovalTimelineItem(
                    id=event.approval_id,
                    run_id=envelope.run_id,
                    turn_id=envelope.turn_id,
                    created_seq=envelope.seq,
                    call_id=envelope.call_id,
                    request=event.request,
                )
            )
            return

        if isinstance(event, ApprovalResolved):
            item = self._find(event.approval_id)
            if isinstance(item, ApprovalTimelineItem):
                item.status = (
                    "approved"
                    if event.decision.value == "allow_once"
                    else "denied"
                )
            return

        if isinstance(event, CompactionApplied):
            self._upsert(
                CompactionTimelineItem(
                    id=f"compaction:{event.compaction_id}",
                    run_id=envelope.run_id,
                    created_seq=envelope.seq,
                    compaction_id=event.compaction_id,
                    through_message_id=event.through_message_id,
                    replaced_count=event.replaced_count,
                )
            )
            return

        if isinstance(event, UsageUpdated):
            target = self._find(envelope.message_id or "")
            if isinstance(target, AssistantTimelineItem):
                target.usage = event.usage
            return

        if isinstance(event, RunStopped):
            run = self._runs.get(envelope.run_id)
            if run is None:
                run = TimelineRun(id=envelope.run_id)
                self._runs[envelope.run_id] = run
            run.status = _run_status(event.status)
            self._finish_streaming_items(envelope.run_id, run.status)

    def push_frame(self, envelope: StreamFrameEnvelope) -> None:
        if envelope.session_id != self.session_id:
            raise ValueError("Stream frame belongs to another session")
        if envelope.frame_seq <= self._frame_seq_by_run.get(envelope.run_id, 0):
            return
        self._frame_seq_by_run[envelope.run_id] = envelope.frame_seq

        frame = envelope.frame
        if isinstance(frame, (AssistantTextDelta, AssistantThinkingDelta)):
            if envelope.message_id in self._committed_assistant_ids:
                return
            item_id = envelope.message_id
            if not item_id:
                return
            item = self._find(item_id)
            if not isinstance(item, AssistantTimelineItem):
                item = AssistantTimelineItem(
                    id=item_id,
                    run_id=envelope.run_id,
                    turn_id=envelope.turn_id,
                    created_seq=self._next_local_seq(),
                )
                self._items.append(item)
            if isinstance(frame, AssistantTextDelta):
                item.text += frame.delta
            else:
                item.thinking += frame.delta
                item.thinking_done = False
            return

        if isinstance(frame, AssistantThinkingDone):
            if envelope.message_id in self._committed_assistant_ids:
                return
            item = self._find(envelope.message_id or "")
            if isinstance(item, AssistantTimelineItem):
                item.thinking_done = True
            return

        if isinstance(frame, ToolArgumentsDelta):
            if envelope.call_id in self._completed_tool_ids:
                return
            item = self._find(envelope.call_id or "")
            if not isinstance(item, ToolTimelineItem):
                item = ToolTimelineItem(
                    id=envelope.call_id or "",
                    run_id=envelope.run_id,
                    turn_id=envelope.turn_id,
                    created_seq=self._next_local_seq(),
                    name="",
                    arguments={},
                )
                self._items.append(item)
            item.arguments_text += frame.delta
            return

        if isinstance(frame, ToolStatusChanged):
            if envelope.call_id in self._completed_tool_ids:
                return
            item = self._find(envelope.call_id or "")
            if not isinstance(item, ToolTimelineItem):
                item = ToolTimelineItem(
                    id=envelope.call_id or "",
                    run_id=envelope.run_id,
                    turn_id=envelope.turn_id,
                    created_seq=self._next_local_seq(),
                    name="",
                    arguments={},
                )
                self._items.append(item)
            item.status = "running" if frame.status == "running" else "requested"

    def snapshot(self) -> TimelineSnapshot:
        return TimelineSnapshot(
            session_id=self.session_id,
            last_seq=self._last_seq,
            items=deepcopy(self._items),
            runs=deepcopy(self._runs),
            frame_seq_by_run=dict(self._frame_seq_by_run),
        )

    def _find(self, item_id: str) -> TimelineItem | None:
        return next((item for item in self._items if item.id == item_id), None)

    def _upsert(self, item: TimelineItem) -> None:
        existing = self._find(item.id)
        if existing is None:
            self._items.append(item)
            return
        index = self._items.index(existing)
        self._items[index] = item

    def _upsert_tool(
        self,
        envelope: CommittedEventEnvelope,
        call: ToolCall,
        *,
        status: Literal["requested", "running"],
    ) -> None:
        item = self._find(call.id)
        if isinstance(item, ToolTimelineItem):
            item.name = call.name or item.name
            item.arguments = call.arguments
            item.status = status
            return
        self._items.append(
            ToolTimelineItem(
                id=call.id,
                run_id=envelope.run_id,
                turn_id=envelope.turn_id,
                created_seq=envelope.seq,
                name=call.name,
                arguments=call.arguments,
                status=status,
            )
        )

    def _next_local_seq(self) -> int:
        self._local_seq = max(self._last_seq, self._local_seq) + 1
        return self._local_seq

    def _finish_streaming_items(
        self,
        run_id: str,
        run_status: Literal["completed", "cancelled", "error"],
    ) -> None:
        assistant_status: Literal["completed", "error", "aborted"] = (
            "aborted"
            if run_status == "cancelled"
            else "error"
            if run_status == "error"
            else "completed"
        )
        for item in self._items:
            if (
                isinstance(item, AssistantTimelineItem)
                and item.run_id == run_id
                and item.status == "streaming"
            ):
                item.status = assistant_status


def _user_display_text(event: UserMessageCommitted) -> str:
    if event.display_text is not None:
        return event.display_text
    text = _user_text(event.message.content)
    for marker in (
        "\n\nAttached files (inspect with tools if needed):",
        "\n\nAttached files (inspect with tools or commands if needed):",
        "\n\nAttached files (use extract_document_text for",
    ):
        if marker in text:
            return text.split(marker, 1)[0]
    return text


def _user_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.text for block in content if isinstance(block, TextContent)
        )
    return ""


def _result_text(result: object) -> str:
    content = getattr(result, "content", [])
    return "\n".join(
        block.text for block in content if isinstance(block, TextContent)
    )


def _run_status(
    status: Literal["completed", "aborted", "error", "max_steps", "input_blocked"],
) -> Literal["completed", "cancelled", "error"]:
    if status == "completed":
        return "completed"
    if status in ("aborted", "input_blocked"):
        return "cancelled"
    return "error"


__all__ = [
    "ApprovalTimelineItem",
    "AssistantTimelineItem",
    "CompactionTimelineItem",
    "TimelineItem",
    "TimelineProjector",
    "TimelineRun",
    "TimelineSnapshot",
    "ToolTimelineItem",
    "UserTimelineItem",
]

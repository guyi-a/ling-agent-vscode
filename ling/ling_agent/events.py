"""Committed session events and ephemeral stream frames."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Literal, Protocol, TypeAlias

from ling_ai.types import (
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from .approvals import ApprovalDecision, ApprovalRequest
from .attachments import UserAttachment
from .changes import RunChange
from .selections import UserSelection


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True, frozen=True)
class RunStarted:
    continuation: bool = False


@dataclass(slots=True, frozen=True)
class UserMessageCommitted:
    message: UserMessage
    attachments: tuple[UserAttachment, ...] = ()
    selections: tuple[UserSelection, ...] = ()
    display_text: str | None = None


@dataclass(slots=True, frozen=True)
class AssistantMessageCommitted:
    message: AssistantMessage


@dataclass(slots=True, frozen=True)
class ToolExecutionStarted:
    call: ToolCall


@dataclass(slots=True, frozen=True)
class ToolResultCommitted:
    result: ToolResultMessage


@dataclass(slots=True, frozen=True)
class ApprovalRequested:
    approval_id: str
    request: ApprovalRequest


@dataclass(slots=True, frozen=True)
class ApprovalResolved:
    approval_id: str
    request: ApprovalRequest
    decision: ApprovalDecision


@dataclass(slots=True, frozen=True)
class UsageUpdated:
    usage: Usage


@dataclass(slots=True, frozen=True)
class RunStopped:
    status: Literal["completed", "aborted", "error", "max_steps", "input_blocked"]
    message: AssistantMessage | None = None


@dataclass(slots=True, frozen=True)
class CompactionApplied:
    compaction_id: str
    through_message_id: str
    summary: UserMessage
    replaced_message_ids: tuple[str, ...]
    replaced_count: int
    estimated_tokens: int


@dataclass(slots=True, frozen=True)
class RunChangesCommitted:
    changes: tuple[RunChange, ...]


@dataclass(slots=True, frozen=True)
class RunChangesReverted:
    pass


CommittedEvent: TypeAlias = (
    RunStarted
    | UserMessageCommitted
    | AssistantMessageCommitted
    | ToolExecutionStarted
    | ToolResultCommitted
    | ApprovalRequested
    | ApprovalResolved
    | CompactionApplied
    | RunChangesCommitted
    | RunChangesReverted
    | UsageUpdated
    | RunStopped
)


@dataclass(slots=True, frozen=True)
class CommittedEventEnvelope:
    session_id: str
    seq: int
    event_id: str
    run_id: str
    turn_id: str
    step: int
    event: CommittedEvent
    emitted_at: int = field(default_factory=_now_ms)
    message_id: str | None = None
    call_id: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise ValueError("seq must be at least 1")
        if self.step < 0:
            raise ValueError("step must not be negative")
        if not self.event_id:
            raise ValueError("event_id must not be empty")


@dataclass(slots=True, frozen=True)
class AssistantTextDelta:
    delta: str


@dataclass(slots=True, frozen=True)
class AssistantThinkingDelta:
    delta: str


@dataclass(slots=True, frozen=True)
class AssistantThinkingDone:
    """Marks the end of one assistant thinking block."""


@dataclass(slots=True, frozen=True)
class ToolArgumentsDelta:
    delta: str


@dataclass(slots=True, frozen=True)
class ToolStatusChanged:
    status: Literal["requested", "running"]


StreamFrame: TypeAlias = (
    AssistantTextDelta
    | AssistantThinkingDelta
    | AssistantThinkingDone
    | ToolArgumentsDelta
    | ToolStatusChanged
)


@dataclass(slots=True, frozen=True)
class StreamFrameEnvelope:
    session_id: str
    run_id: str
    turn_id: str
    step: int
    frame_seq: int
    frame: StreamFrame
    emitted_at: int = field(default_factory=_now_ms)
    message_id: str | None = None
    call_id: str | None = None

    def __post_init__(self) -> None:
        if self.frame_seq < 1:
            raise ValueError("frame_seq must be at least 1")
        if self.step < 0:
            raise ValueError("step must not be negative")


class EventSink(Protocol):
    def emit_committed(
        self,
        envelope: CommittedEventEnvelope,
    ) -> None | Awaitable[None]:
        """Publish one durably committed event."""

    def emit_stream_frame(
        self,
        envelope: StreamFrameEnvelope,
    ) -> None | Awaitable[None]:
        """Publish one ephemeral stream frame."""


__all__ = [
    "ApprovalRequested",
    "ApprovalResolved",
    "AssistantMessageCommitted",
    "AssistantTextDelta",
    "AssistantThinkingDelta",
    "AssistantThinkingDone",
    "CompactionApplied",
    "CommittedEvent",
    "CommittedEventEnvelope",
    "EventSink",
    "RunChangesCommitted",
    "RunChangesReverted",
    "RunStarted",
    "RunStopped",
    "StreamFrame",
    "StreamFrameEnvelope",
    "ToolArgumentsDelta",
    "ToolExecutionStarted",
    "ToolResultCommitted",
    "ToolStatusChanged",
    "UsageUpdated",
    "UserMessageCommitted",
]

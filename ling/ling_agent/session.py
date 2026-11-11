"""Committed session orchestration and frontend event publication."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from ling_ai.events import (
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
)
from ling_ai.types import (
    AssistantMessage,
    Context,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from ling_core import (
    AgentFatalError,
    HookRegistry,
    PostToolUse,
    PreToolUse,
    ToolExecution,
    ToolRegistry,
)

from .approvals import (
    ApprovalDecision,
    ApprovalHandler,
    ApprovalManager,
    ApprovalRequest,
)
from .attachments import UserAttachment, friendly_provider_error
from .selections import UserSelection
from .compaction import CompactionRecord
from .effects import ApprovalMode
from .events import (
    ApprovalRequested,
    ApprovalResolved,
    AssistantMessageCommitted,
    AssistantTextDelta,
    AssistantThinkingDelta,
    AssistantThinkingDone,
    CompactionApplied,
    CommittedEvent,
    CommittedEventEnvelope,
    EventSink,
    RunStarted,
    RunStopped,
    StreamFrame,
    ToolArgumentsDelta,
    ToolExecutionStarted,
    ToolResultCommitted,
    ToolStatusChanged,
    UserMessageCommitted,
)
from .storage import (
    SQLiteSessionStore,
    SessionRecord,
    StorageError,
)
from .streaming import StreamFrameBuffer


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


class SessionError(RuntimeError):
    pass


class SessionStateError(SessionError):
    pass


class SessionPersistenceError(AgentFatalError):
    def __init__(self, message: str, *, cause: StorageError) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(slots=True, frozen=True)
class SessionTurn:
    run_id: str
    turn_id: str
    message_id: str
    step: int


class Session:
    """Own one session's committed sequence and live run state."""

    def __init__(
        self,
        record: SessionRecord,
        store: SQLiteSessionStore,
        *,
        context: Context | None = None,
        sink: EventSink | None = None,
        frames: StreamFrameBuffer | None = None,
        approvals: ApprovalManager | None = None,
    ) -> None:
        self.record = record
        self.store = store
        self.context = context or Context()
        self.sink = sink
        self.frames = frames or StreamFrameBuffer()
        self.approvals = approvals or ApprovalManager()
        self._last_seq = record.last_seq
        self._commit_lock = asyncio.Lock()
        self._run_id: str | None = None
        self._current_turn: SessionTurn | None = None
        self._last_completed_turn: SessionTurn | None = None
        self._turn_counter = 0
        self._approval_ids: dict[str, str] = {}
        self._run_has_images = False
        self.active_compaction: CompactionRecord | None = None

    @classmethod
    async def create(
        cls,
        store: SQLiteSessionStore,
        *,
        workspace_root: str | Path,
        title: str | None = None,
        context: Context | None = None,
        sink: EventSink | None = None,
        frames: StreamFrameBuffer | None = None,
        approvals: ApprovalManager | None = None,
    ) -> "Session":
        now = _now_ms()
        record = SessionRecord(
            session_id=_new_id("session"),
            workspace_root=Path(workspace_root),
            title=title,
            status="idle",
            created_at=now,
            updated_at=now,
        )
        await store.upsert_session(record)
        return cls(
            record,
            store,
            context=context,
            sink=sink,
            frames=frames,
            approvals=approvals,
        )

    @classmethod
    async def load(
        cls,
        store: SQLiteSessionStore,
        session_id: str,
        *,
        context: Context | None = None,
        sink: EventSink | None = None,
        frames: StreamFrameBuffer | None = None,
        approvals: ApprovalManager | None = None,
    ) -> "Session":
        record = await store.get_session(session_id)
        if record is None:
            raise SessionStateError(f"Unknown session: {session_id}")
        session = cls(
            record,
            store,
            context=context,
            sink=sink,
            frames=frames,
            approvals=approvals,
        )
        events = await store.load_committed(session_id)
        session.active_compaction = _find_latest_compaction(events)
        return session

    @property
    def session_id(self) -> str:
        return self.record.session_id

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def approval_mode(self) -> ApprovalMode:
        return self.record.approval_mode

    async def set_approval_mode(self, mode: ApprovalMode) -> None:
        updated_at = _now_ms()
        await self.store.set_approval_mode(
            self.session_id,
            mode,
            updated_at=updated_at,
        )
        self.record = replace(
            self.record,
            approval_mode=mode,
            updated_at=updated_at,
        )

    async def commit_compaction(self, record: CompactionRecord) -> None:
        if self._run_id is None:
            raise SessionStateError("No active run")
        turn_id = (
            self._current_turn.turn_id
            if self._current_turn is not None
            else self._last_completed_turn.turn_id
            if self._last_completed_turn is not None
            else self._run_id
        )
        await self._commit(
            CompactionApplied(
                compaction_id=record.id,
                through_message_id=record.through_message_id,
                summary=UserMessage(
                    content=record.summary,
                    timestamp=_now_ms(),
                ),
                replaced_message_ids=tuple(record.replaced_message_ids),
                replaced_count=record.replaced_count,
                estimated_tokens=record.estimated_tokens,
            ),
            step=self._turn_counter,
            turn_id=turn_id,
        )
        self.active_compaction = record

    async def start_run(
        self,
        user_message: UserMessage,
        *,
        attachments: tuple[UserAttachment, ...] = (),
        selections: tuple[UserSelection, ...] = (),
        display_text: str | None = None,
        continuation: bool = False,
    ) -> str:
        if self._run_id is not None:
            raise SessionStateError("A run is already active")
        run_id = _new_id("run")
        self._run_id = run_id
        self._turn_counter = 0
        self._current_turn = None
        self._last_completed_turn = None
        self._run_has_images = any(
            attachment.kind == "image" for attachment in attachments
        )
        message_id = _new_id("message")

        await self._commit(
            RunStarted(continuation=continuation),
            step=0,
            turn_id=run_id,
        )
        await self._commit(
            UserMessageCommitted(
                message=user_message,
                attachments=attachments,
                selections=selections,
                display_text=display_text,
            ),
            step=0,
            turn_id=run_id,
            message_id=message_id,
        )
        self.context.messages.append(user_message)
        return run_id

    async def start_continuation_run(self) -> str:
        if self._run_id is not None:
            raise SessionStateError("A run is already active")
        run_id = _new_id("run")
        self._run_id = run_id
        self._turn_counter = 0
        self._current_turn = None
        self._last_completed_turn = None
        self._run_has_images = False
        await self._commit(
            RunStarted(continuation=True),
            step=0,
            turn_id=run_id,
        )
        return run_id

    def begin_turn(self) -> SessionTurn:
        if self._run_id is None:
            raise SessionStateError("No active run")
        if self._current_turn is not None:
            raise SessionStateError("A model turn is already active")
        self._turn_counter += 1
        turn = SessionTurn(
            run_id=self._run_id,
            turn_id=f"{self._run_id}:turn:{self._turn_counter}",
            message_id=_new_id("message"),
            step=self._turn_counter,
        )
        self._current_turn = turn
        return turn

    async def handle_model_event(self, event: object) -> None:
        if isinstance(event, StartEvent):
            self.begin_turn()
            return

        turn = self._current_turn
        if turn is None:
            # A provider can fail before emitting StartEvent (for example a
            # connection error). The terminal event still needs a turn so the
            # assistant message and its error are committed.
            turn = self.begin_turn()

        if isinstance(event, TextDeltaEvent):
            await self.publish_frame(
                AssistantTextDelta(delta=event.delta),
                message_id=turn.message_id,
            )
            return

        if isinstance(event, ThinkingDeltaEvent):
            await self.publish_frame(
                AssistantThinkingDelta(delta=event.delta),
                message_id=turn.message_id,
            )
            return

        if isinstance(event, ThinkingEndEvent):
            await self.publish_frame(
                AssistantThinkingDone(),
                message_id=turn.message_id,
            )
            return

        if isinstance(event, ToolCallStartEvent):
            call = event.partial.content[event.content_index]
            if isinstance(call, ToolCall) and call.id:
                await self.publish_frame(
                    ToolStatusChanged(status="requested"),
                    call_id=call.id,
                )
            return

        if isinstance(event, ToolCallDeltaEvent):
            call = event.partial.content[event.content_index]
            if isinstance(call, ToolCall) and call.id and event.delta:
                await self.publish_frame(
                    ToolArgumentsDelta(delta=event.delta),
                    call_id=call.id,
                )
            return

        if isinstance(event, DoneEvent):
            await self.commit_assistant_message(event.message, turn=turn)
            self._last_completed_turn = turn
            self._current_turn = None
            return

        if isinstance(event, ErrorEvent):
            await self.commit_assistant_message(event.error, turn=turn)
            self._last_completed_turn = turn
            self._current_turn = None

    async def commit_assistant_message(
        self,
        message: AssistantMessage,
        *,
        turn: SessionTurn | None = None,
    ) -> str:
        selected = turn or self._current_turn or self._last_completed_turn
        if selected is None:
            raise SessionStateError("No model turn to commit")
        if message.stop_reason == "error" and self._run_has_images:
            message.error_message = friendly_provider_error(
                message.error_message
            )
        envelope = await self._commit(
            AssistantMessageCommitted(message=message),
            step=selected.step,
            turn_id=selected.turn_id,
            message_id=selected.message_id,
        )
        self._last_completed_turn = selected
        self._current_turn = None
        return envelope.message_id or selected.message_id

    async def commit_tool_started(self, call: ToolCall) -> None:
        turn = self._require_tool_turn()
        await self._commit(
            ToolExecutionStarted(call=call),
            step=turn.step,
            turn_id=turn.turn_id,
            call_id=call.id,
        )

    async def commit_tool_result(self, result: ToolResultMessage) -> None:
        turn = self._require_tool_turn()
        await self._commit(
            ToolResultCommitted(result=result),
            step=turn.step,
            turn_id=turn.turn_id,
            call_id=result.tool_call_id,
        )

    async def commit_approval_requested(
        self,
        request: ApprovalRequest,
    ) -> str:
        turn = self._require_tool_turn()
        approval_id = request.approval_id or _new_id("approval")
        self._approval_ids[request.call_id] = approval_id
        await self._commit(
            ApprovalRequested(approval_id=approval_id, request=request),
            step=turn.step,
            turn_id=turn.turn_id,
            call_id=request.call_id or None,
            approval_id=approval_id,
        )
        return approval_id

    async def commit_approval_resolved(
        self,
        approval_id: str,
        request: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> None:
        turn = self._require_tool_turn()
        await self._commit(
            ApprovalResolved(
                approval_id=approval_id,
                request=request,
                decision=decision,
            ),
            step=turn.step,
            turn_id=turn.turn_id,
            call_id=request.call_id or None,
            approval_id=approval_id,
        )

    async def commit_denied_tool_result(
        self,
        request: ApprovalRequest,
        *,
        reason: str = "Permission denied",
    ) -> None:
        if not request.call_id:
            return
        await self.commit_tool_result(
            ToolResultMessage(
                tool_call_id=request.call_id,
                tool_name=request.tool_name,
                content=[TextContent(f"Error: {reason}")],
                is_error=True,
                timestamp=_now_ms(),
            )
        )

    async def stop_run(
        self,
        status: Literal[
            "completed",
            "aborted",
            "error",
            "max_steps",
            "input_blocked",
        ],
        message: AssistantMessage | None = None,
    ) -> None:
        if self._run_id is None:
            return
        self.approvals.cancel_all("Run stopped")
        turn_id = (
            self._current_turn.turn_id
            if self._current_turn is not None
            else self._last_completed_turn.turn_id
            if self._last_completed_turn is not None
            else self._run_id
        )
        message_id = (
            self._current_turn.message_id
            if self._current_turn is not None
            else self._last_completed_turn.message_id
            if self._last_completed_turn is not None
            else None
        )
        await self._commit(
            RunStopped(status=status, message=message),
            step=self._turn_counter,
            turn_id=turn_id,
            message_id=message_id,
        )
        self.frames.clear_run(self._run_id)
        self._run_id = None
        self._current_turn = None
        self._last_completed_turn = None

    async def publish_frame(
        self,
        frame: StreamFrame,
        *,
        message_id: str | None = None,
        call_id: str | None = None,
    ) -> None:
        turn = self._current_turn or self._last_completed_turn
        if self._run_id is None or turn is None:
            raise SessionStateError("No active model turn")
        envelope = self.frames.append(
            session_id=self.session_id,
            run_id=self._run_id,
            turn_id=turn.turn_id,
            step=turn.step,
            frame=frame,
            message_id=message_id,
            call_id=call_id,
        )
        if self.sink is not None:
            try:
                result = self.sink.emit_stream_frame(envelope)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

    def register_hooks(self, hooks: HookRegistry) -> None:
        hooks.register(PreToolUse, self._pre_tool_hook)
        hooks.register(PostToolUse, self._post_tool_hook)

    async def _pre_tool_hook(self, event: PreToolUse) -> None:
        await self.commit_tool_started(event.call)

    async def _post_tool_hook(self, event: PostToolUse) -> None:
        execution = ToolExecution(
            output=event.output,
            is_error=event.is_error,
        )
        await self.commit_tool_result(
            ToolRegistry.to_result(event.call, execution)
        )

    def _require_tool_turn(self) -> SessionTurn:
        turn = self._last_completed_turn or self._current_turn
        if self._run_id is None or turn is None:
            raise SessionStateError("No active tool turn")
        return turn

    async def _commit(
        self,
        event: CommittedEvent,
        *,
        step: int,
        turn_id: str,
        message_id: str | None = None,
        call_id: str | None = None,
        approval_id: str | None = None,
    ) -> CommittedEventEnvelope:
        if self._run_id is None:
            raise SessionStateError("No active run")
        async with self._commit_lock:
            seq = self._last_seq + 1
            envelope = CommittedEventEnvelope(
                session_id=self.session_id,
                seq=seq,
                event_id=_new_id("event"),
                run_id=self._run_id,
                turn_id=turn_id,
                step=step,
                event=event,
                message_id=message_id,
                call_id=call_id,
                approval_id=approval_id,
            )
            try:
                await self.store.append_committed(envelope)
            except StorageError as exc:
                raise SessionPersistenceError(
                    f"Failed to persist committed event: {exc}",
                    cause=exc,
                ) from exc
            self._last_seq = seq

        if self.sink is not None:
            try:
                result = self.sink.emit_committed(envelope)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
        return envelope


def _find_latest_compaction(events) -> CompactionRecord | None:
    latest: CompactionRecord | None = None
    for envelope in events:
        event = envelope.event
        if not isinstance(event, CompactionApplied):
            continue
        latest = CompactionRecord(
            id=event.compaction_id,
            through_message_id=event.through_message_id,
            summary=_message_text(event.summary),
            replaced_message_ids=tuple(event.replaced_message_ids),
            replaced_count=event.replaced_count,
            estimated_tokens=event.estimated_tokens,
        )
    return latest


def _message_text(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        block.text
        for block in message.content
        if isinstance(block, TextContent)
    )


class SessionApprovalHandler:
    """Persist approval request/decision around an application handler."""

    def __init__(
        self,
        session: Session,
        handler: ApprovalHandler,
    ) -> None:
        self._session = session
        self._handler = handler

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        approval_id = await self._session.commit_approval_requested(request)
        try:
            result = self._handler.request(request)
            if inspect.isawaitable(result):
                result = await result
        except AgentFatalError:
            raise
        except BaseException as exc:
            reason = (
                "interrupted"
                if isinstance(exc, asyncio.CancelledError)
                else "Approval handler failed"
            )
            await self._session.commit_approval_resolved(
                approval_id,
                request,
                ApprovalDecision.DENY,
            )
            await self._session.commit_denied_tool_result(
                request,
                reason=reason,
            )
            raise

        if not isinstance(result, ApprovalDecision):
            result = ApprovalDecision.DENY
        if request.cancel_event is not None and request.cancel_event.is_set():
            result = ApprovalDecision.DENY
        await self._session.commit_approval_resolved(
            approval_id,
            request,
            result,
        )
        if result is ApprovalDecision.DENY:
            await self._session.commit_denied_tool_result(request)
        return result


__all__ = [
    "Session",
    "SessionApprovalHandler",
    "SessionError",
    "SessionPersistenceError",
    "SessionStateError",
    "SessionTurn",
]

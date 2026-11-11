"""Project Ling agent events into the Webview UI contract."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Literal, Sequence

from ling_agent import (
    ApprovalTimelineItem,
    AssistantTextDelta,
    AssistantThinkingDelta,
    AssistantThinkingDone,
    AssistantTimelineItem,
    CompactionTimelineItem,
    CommittedEventEnvelope,
    QuestionRequest,
    StreamFrameEnvelope,
    TimelineItem,
    TimelineProjector,
    TimelineSnapshot,
    ToolTimelineItem,
    UserTimelineItem,
    question_request_to_dict,
)

UiEvent = dict[str, Any]
EmitUiEvent = Callable[[UiEvent], Awaitable[None]]


def snapshot_to_ui(
    snapshot: TimelineSnapshot,
    *,
    approval_mode: str | None = None,
    pending_questions: Sequence[QuestionRequest] | None = None,
    queued_messages: Sequence[dict[str, Any]] | None = None,
) -> UiEvent:
    pending: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    pending_call_ids = {
        item.call_id
        for item in snapshot.items
        if isinstance(item, ApprovalTimelineItem)
        and item.status == "pending"
        and item.call_id
    }

    for item in snapshot.items:
        if isinstance(item, UserTimelineItem):
            entries.append(
                _message_entry(
                    item_id=item.id,
                    role="user",
                    content=item.text,
                    streaming=False,
                    attachments=[
                        {
                            "relativePath": attachment.relative_path,
                            "name": attachment.name,
                            "mediaType": attachment.media_type,
                            "kind": attachment.kind,
                        }
                        for attachment in item.attachments
                    ],
                    selections=[
                        {
                            "kind": selection.kind,
                            "label": selection.label,
                            "lineCount": selection.line_count,
                            **(
                                {"path": selection.path}
                                if selection.path is not None
                                else {}
                            ),
                            **(
                                {"startLine": selection.start_line}
                                if selection.start_line is not None
                                else {}
                            ),
                            **(
                                {"endLine": selection.end_line}
                                if selection.end_line is not None
                                else {}
                            ),
                        }
                        for selection in item.selections
                    ],
                )
            )
            continue

        if isinstance(item, AssistantTimelineItem):
            entries.append(
                _message_entry(
                    item_id=item.id,
                    role="assistant",
                    content=item.text,
                    streaming=item.status == "streaming",
                    thinking=item.thinking,
                    thinking_done=item.thinking_done,
                )
            )
            continue

        if isinstance(item, ToolTimelineItem):
            entries.append(
                _tool_entry(
                    item,
                    waiting_for_approval=item.id in pending_call_ids,
                )
            )
            continue

        if isinstance(item, ApprovalTimelineItem):
            request = _approval_request(item)
            if item.status == "pending":
                pending.append(request)
            else:
                entries.append(
                    {
                        "kind": "approval",
                        "approval": {
                            "id": f"result-{item.id}",
                            "approvalId": item.id,
                            "approved": item.status == "approved",
                            "target": request["target"],
                            "action": request["action"],
                        },
                    }
                )
            continue

        if isinstance(item, CompactionTimelineItem):
            entries.append(
                {
                    "kind": "compaction",
                    "compaction": {
                        "id": item.id,
                        "compactionId": item.compaction_id,
                        "replacedCount": item.replaced_count,
                    },
                }
            )

    phase = _run_phase(snapshot, has_pending=bool(pending))
    result: UiEvent = {
        "phase": phase,
        "entries": entries,
        "pendingApprovals": pending,
        "pendingQuestions": [
            question_request_to_dict(request)
            for request in (pending_questions or ())
        ],
        "queuedMessages": list(queued_messages or ()),
        "error": _run_error(snapshot) if phase == "failed" else None,
        "sessionId": snapshot.session_id,
        "readOnly": False,
    }
    if approval_mode is not None:
        result["approvalMode"] = approval_mode
    return result


class RpcEventSink:
    """Implement the harness EventSink by emitting Webview events."""

    def __init__(
        self,
        emit: EmitUiEvent,
        *,
        approval_mode: Callable[[], str] | None = None,
        queued_messages: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._emit = emit
        self._approval_mode = approval_mode
        self._queued_messages = queued_messages
        self._projector: TimelineProjector | None = None
        self._session_id: str | None = None

    def seed_committed(self, events: list[CommittedEventEnvelope]) -> None:
        if not events:
            return
        projector = self._projector_for(events[0].session_id)
        for envelope in events:
            projector.push_committed(envelope)

    async def emit_committed(self, envelope: CommittedEventEnvelope) -> None:
        projector = self._projector_for(envelope.session_id)
        projector.push_committed(envelope)
        mode = self._approval_mode() if self._approval_mode is not None else None
        queued = (
            self._queued_messages()
            if self._queued_messages is not None
            else None
        )
        snapshot = snapshot_to_ui(
            projector.snapshot(),
            approval_mode=mode,
            queued_messages=queued,
        )
        await self._emit(
            {
                "type": "session.snapshot",
                "snapshot": snapshot,
            }
        )

    async def emit_stream_frame(self, envelope: StreamFrameEnvelope) -> None:
        projector = self._projector_for(envelope.session_id)
        projector.push_frame(envelope)

        if isinstance(envelope.frame, AssistantTextDelta):
            await self._emit(
                {
                    "type": "message.delta",
                    "messageId": envelope.message_id,
                    "role": "assistant",
                    "delta": envelope.frame.delta,
                }
            )
        elif isinstance(envelope.frame, AssistantThinkingDelta):
            await self._emit(
                {
                    "type": "thinking.delta",
                    "messageId": envelope.message_id,
                    "delta": envelope.frame.delta,
                }
            )
        elif isinstance(envelope.frame, AssistantThinkingDone):
            await self._emit(
                {
                    "type": "thinking.completed",
                    "messageId": envelope.message_id,
                }
            )

    def _projector_for(self, session_id: str) -> TimelineProjector:
        if self._projector is None:
            self._session_id = session_id
            self._projector = TimelineProjector(session_id)
        if self._session_id != session_id:
            raise RuntimeError("UI event sink received multiple session ids")
        return self._projector


def _message_entry(
    *,
    item_id: str,
    role: Literal["user", "assistant"],
    content: str,
    streaming: bool,
    thinking: str = "",
    thinking_done: bool = False,
    attachments: list[dict[str, Any]] | None = None,
    selections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": item_id,
        "role": role,
        "content": content,
        "streaming": streaming,
    }
    if thinking:
        message["thinking"] = thinking
        message["thinkingDone"] = thinking_done
    if attachments:
        message["attachments"] = attachments
    if selections:
        message["selections"] = selections
    return {
        "kind": "message",
        "message": message,
    }


def _tool_entry(
    item: ToolTimelineItem,
    *,
    waiting_for_approval: bool,
) -> dict[str, Any]:
    details = [
        f"Tool: {item.name or '(unknown)'}",
        f"Arguments: {json.dumps(item.arguments, ensure_ascii=False, sort_keys=True)}",
    ]
    if item.output:
        details.append(f"Output: {item.output}")
    if item.error:
        details.append(f"Error: {item.error}")

    return {
        "kind": "tool",
        "tool": {
            "id": item.id,
            "action": _tool_action(item.name),
            "target": _tool_target(item.name, item.arguments),
            "status": (
                "Waiting"
                if waiting_for_approval
                else _tool_status(item.status)
            ),
            "details": details,
        },
    }


def _approval_request(item: ApprovalTimelineItem) -> dict[str, Any]:
    request = item.request
    return {
        "id": item.id,
        "tool": request.tool_name,
        "action": _tool_action(request.tool_name),
        "target": _tool_target(request.tool_name, request.arguments),
        "reason": request.reason,
        "arguments": request.arguments,
        "effectDigest": request.effect_digest,
        "details": [
            f"Tool: {request.tool_name}",
            f"Arguments: {json.dumps(request.arguments, ensure_ascii=False, sort_keys=True)}",
            f"Effect digest: {request.effect_digest}",
        ],
    }


def _tool_action(
    name: str,
) -> Literal["Read", "Edit", "Run", "Skill"]:
    if name == "load_skill":
        return "Skill"
    if name in (
        "read_file",
        "list_files",
        "grep",
        "glob",
        "extract_document_text",
    ):
        return "Read"
    if name in ("write_file", "edit_file"):
        return "Edit"
    return "Run"


def _tool_target(name: str, arguments: dict[str, object]) -> str:
    for key in ("path", "command", "pattern", "name"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return name


def _tool_status(
    status: Literal["requested", "running", "completed", "failed", "cancelled"],
) -> Literal["Running", "Waiting", "Completed", "Failed", "Cancelled"]:
    return {
        "requested": "Waiting",
        "running": "Running",
        "completed": "Completed",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }[status]


def _run_phase(
    snapshot: TimelineSnapshot,
    *,
    has_pending: bool,
) -> Literal[
    "idle",
    "running",
    "waiting_approval",
    "cancelled",
    "failed",
]:
    if has_pending:
        return "waiting_approval"
    if not snapshot.runs:
        return "idle"
    status = next(reversed(snapshot.runs.values())).status
    if status == "running":
        return "running"
    if status == "cancelled":
        return "cancelled"
    if status == "error":
        return "failed"
    return "idle"


def _run_error(snapshot: TimelineSnapshot) -> str | None:
    for item in reversed(snapshot.items):
        if isinstance(item, AssistantTimelineItem) and item.error:
            return item.error
    return "The agent run failed"


__all__ = ["EmitUiEvent", "RpcEventSink", "UiEvent", "snapshot_to_ui"]

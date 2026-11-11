"""Project Ling agent events into the Webview UI contract."""

from __future__ import annotations

import difflib
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


_IO_TOOLS = frozenset(
    {
        "read_file",
        "list_files",
        "grep",
        "glob",
        "search_codebase",
        "extract_document_text",
        "load_skill",
        "ask_user",
    }
)

_MAX_DIFF_ROWS = 400
_MAX_DIFF_CHARS = 40_000


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

    tool: dict[str, Any] = {
        "id": item.id,
        "action": _tool_action(item.name),
        "target": _tool_target(item.name, item.arguments),
        "status": (
            "Waiting"
            if waiting_for_approval
            else _tool_status(item.status)
        ),
        "details": details,
    }
    detail = _tool_detail(item)
    if detail is not None:
        tool["detail"] = detail
    return {"kind": "tool", "tool": tool}


def _tool_detail(item: ToolTimelineItem) -> dict[str, Any] | None:
    arguments = item.arguments if isinstance(item.arguments, dict) else {}
    metadata = item.details if isinstance(item.details, dict) else {}

    if item.name == "edit_file":
        path = arguments.get("path")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not (
            isinstance(path, str)
            and isinstance(old_text, str)
            and isinstance(new_text, str)
        ):
            return None
        start = metadata.get("startLine")
        start_line = start if isinstance(start, int) and start > 0 else 1
        rows, added, removed, truncated = _diff_rows(
            old_text,
            new_text,
            start_line,
        )
        return {
            "kind": "diff",
            "path": path,
            "rows": rows,
            "added": added,
            "removed": removed,
            "truncated": truncated,
        }

    if item.name == "write_file":
        path = arguments.get("path")
        content = arguments.get("content")
        if not (isinstance(path, str) and isinstance(content, str)):
            return None
        previous = metadata.get("previousContent")
        rows, added, removed, truncated = _diff_rows(
            previous if isinstance(previous, str) else "",
            content,
            1,
        )
        if metadata.get("previousTruncated"):
            truncated = True
        return {
            "kind": "diff",
            "path": path,
            "rows": rows,
            "added": added,
            "removed": removed,
            "truncated": truncated,
        }

    if item.name == "run_command":
        command = arguments.get("command")
        return {
            "kind": "io",
            "input": command if isinstance(command, str) else "",
            "output": item.output,
            **({"error": item.error} if item.error else {}),
        }

    if item.name in _IO_TOOLS:
        return {
            "kind": "io",
            "input": _format_tool_input(item.name, arguments),
            "output": item.output,
            **({"error": item.error} if item.error else {}),
        }

    return None


def _format_tool_input(
    name: str,
    arguments: dict[str, Any],
) -> str:
    if name == "read_file":
        path = arguments.get("path")
        if not isinstance(path, str):
            path = ""
        start = arguments.get("start_line")
        limit = arguments.get("line_limit")
        if isinstance(start, int) or isinstance(limit, int):
            return f"{path} (line {start or 1}, limit {limit or 'end'})"
        return path
    if name == "load_skill":
        value = arguments.get("name")
        return value if isinstance(value, str) else ""
    return json.dumps(arguments, ensure_ascii=False, indent=2)


def _diff_rows(
    old_text: str,
    new_text: str,
    start_line: int,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    truncated = False
    if len(old_text) + len(new_text) > _MAX_DIFF_CHARS:
        old_text = old_text[:_MAX_DIFF_CHARS // 2]
        new_text = new_text[:_MAX_DIFF_CHARS // 2]
        truncated = True

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    rows: list[dict[str, Any]] = []
    added = 0
    removed = 0
    old_line = start_line
    new_line = start_line

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append(
                    {
                        "type": "context",
                        "oldLine": old_line,
                        "newLine": new_line,
                        "text": old_lines[i1 + offset],
                    }
                )
                old_line += 1
                new_line += 1
        elif tag in ("delete", "replace"):
            for offset in range(i2 - i1):
                rows.append(
                    {
                        "type": "remove",
                        "oldLine": old_line,
                        "text": old_lines[i1 + offset],
                    }
                )
                old_line += 1
                removed += 1
        if tag in ("insert", "replace"):
            for offset in range(j2 - j1):
                rows.append(
                    {
                        "type": "add",
                        "newLine": new_line,
                        "text": new_lines[j1 + offset],
                    }
                )
                new_line += 1
                added += 1
        if len(rows) > _MAX_DIFF_ROWS:
            rows = rows[:_MAX_DIFF_ROWS]
            truncated = True
            break

    return rows, added, removed, truncated


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

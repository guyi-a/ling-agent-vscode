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
    ProcessExecEffect,
    QuestionRequest,
    StreamFrameEnvelope,
    TimelineItem,
    TimelineProjector,
    TimelineSnapshot,
    ToolResultCommitted,
    ToolTimelineItem,
    UserTimelineItem,
    question_request_to_dict,
)
from ling_agent.local_settings import command_allow_patterns

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
                    run_id=item.run_id,
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
                    run_id=item.run_id,
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
                        "runId": item.run_id,
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
                    "runId": item.run_id,
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
        "runs": [
            {
                "id": run.id,
                "status": run.status,
                **(
                    {"startedAt": run.started_at}
                    if run.started_at is not None
                    else {}
                ),
                **(
                    {"completedAt": run.completed_at}
                    if run.completed_at is not None
                    else {}
                ),
                **(
                    {
                        "changes": [
                            {
                                "path": change.path,
                                "added": change.added,
                                "removed": change.removed,
                            }
                            for change in run.changes
                        ]
                    }
                    if run.changes
                    else {}
                ),
                **(
                    {"changesReverted": True}
                    if run.changes_reverted
                    else {}
                ),
            }
            for run in snapshot.runs.values()
        ],
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
        event = envelope.event
        if (
            isinstance(event, ToolResultCommitted)
            and not event.result.is_error
            and event.result.tool_name == "create_plan"
        ):
            details = event.result.details
            if isinstance(details, dict):
                plan_path = details.get("path")
                if isinstance(plan_path, str):
                    await self._emit(
                        {
                            "type": "plan.created",
                            "path": plan_path,
                            "sessionId": envelope.session_id,
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
    run_id: str,
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
        "runId": run_id,
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
        "browser_bridge",
        "web_search",
        "web_fetch",
        "memory_list",
        "memory_write",
        "memory_delete",
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
        "name": item.name,
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
    return {
        "kind": "tool",
        "runId": item.run_id,
        "tool": tool,
    }


def _tool_detail(item: ToolTimelineItem) -> dict[str, Any] | None:
    arguments = item.arguments if isinstance(item.arguments, dict) else {}
    metadata = item.details if isinstance(item.details, dict) else {}

    if item.name == "load_skill":
        return _skill_tool_detail(item)

    if item.name in (
        "memory_list",
        "memory_write",
        "memory_delete",
    ):
        return _memory_tool_detail(item)

    if item.name.startswith("mcp_"):
        return {
            "kind": "io",
            "input": _format_tool_input(item.name, arguments),
            "output": item.output,
            **({"error": item.error} if item.error else {}),
        }

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

    if item.name in ("create_plan", "update_plan"):
        path = metadata.get("path")
        text = metadata.get("text")
        if isinstance(path, str) and isinstance(text, str):
            return {
                "kind": "plan",
                "path": path,
                "text": text,
            }
        return None

    if item.name == "todo_write":
        todos = metadata.get("todos")
        if isinstance(todos, list):
            return {"kind": "todo", "todos": todos}
        return None

    if item.name == "spawn_subagent":
        return _subagent_detail(item, arguments, metadata)

    if item.name in _IO_TOOLS:
        return {
            "kind": "io",
            "input": _format_tool_input(item.name, arguments),
            "output": item.output,
            **({"error": item.error} if item.error else {}),
        }

    return None


def _skill_tool_detail(
    item: ToolTimelineItem,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(item.output)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        requested = item.arguments.get("name")
        name = requested if isinstance(requested, str) else "Skill"
    if payload.get("ok") is False:
        message = payload.get("message")
        return {
            "kind": "skill",
            "name": name,
            "description": "",
            "body": "",
            "skillPath": "",
            "ok": False,
            **(
                {"error": message}
                if isinstance(message, str) and message
                else {}
            ),
        }
    description = payload.get("description")
    body = payload.get("body")
    skill_path = payload.get("skill_path")
    if (
        not isinstance(description, str)
        or not isinstance(body, str)
        or not isinstance(skill_path, str)
    ):
        return None
    scripts_path = payload.get("scripts_path")
    return {
        "kind": "skill",
        "name": name,
        "description": description,
        "body": body,
        "skillPath": skill_path,
        "ok": True,
        **(
            {"scriptsPath": scripts_path}
            if isinstance(scripts_path, str)
            else {}
        ),
        **(
            {"truncated": True}
            if payload.get("truncated") is True
            else {}
        ),
    }


def _memory_tool_detail(
    item: ToolTimelineItem,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(item.output)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    operation = {
        "memory_list": "list",
        "memory_write": "write",
        "memory_delete": "delete",
    }[item.name]
    detail: dict[str, Any] = {
        "kind": "memory",
        "operation": operation,
        "ok": payload.get("ok") is not False,
    }
    message = payload.get("message")
    if isinstance(message, str) and message:
        detail["error"] = message
    scope = payload.get("scope")
    if scope in ("workspace", "global"):
        detail["scope"] = scope
    name = payload.get("name")
    if isinstance(name, str) and name:
        detail["name"] = name
    path = payload.get("path")
    if isinstance(path, str) and path:
        detail["path"] = path

    if operation == "list":
        memories: list[dict[str, Any]] = []
        raw_memories = payload.get("memories")
        if isinstance(raw_memories, list):
            for memory in raw_memories:
                if not isinstance(memory, dict):
                    continue
                memory_name = memory.get("name")
                description = memory.get("description")
                memory_scope = memory.get("scope")
                always_apply = memory.get("alwaysApply")
                globs = memory.get("globs")
                memory_path = memory.get("path")
                if (
                    not isinstance(memory_name, str)
                    or not isinstance(description, str)
                    or memory_scope not in ("workspace", "global")
                    or not isinstance(always_apply, bool)
                    or not isinstance(globs, list)
                    or not all(isinstance(value, str) for value in globs)
                    or not isinstance(memory_path, str)
                ):
                    continue
                memories.append(
                    {
                        "name": memory_name,
                        "description": description,
                        "scope": memory_scope,
                        "alwaysApply": always_apply,
                        "globs": globs,
                        "path": memory_path,
                    }
                )
        detail["memories"] = memories
        diagnostics = payload.get("diagnostics")
        if isinstance(diagnostics, list):
            detail["diagnostics"] = [
                value for value in diagnostics if isinstance(value, str)
            ]
    return detail


def _subagent_detail(
    item: ToolTimelineItem,
    arguments: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    description = metadata.get("description")
    if not isinstance(description, str):
        value = arguments.get("description")
        description = value if isinstance(value, str) else ""
    summary = metadata.get("summary")
    if not isinstance(summary, str):
        summary = ""
        if item.output:
            try:
                parsed = json.loads(item.output)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                value = parsed.get("summary")
                if isinstance(value, str):
                    summary = value
    trace = metadata.get("trace")
    entries = (
        [
            entry
            for entry in trace
            if isinstance(entry, dict)
        ]
        if isinstance(trace, list)
        else []
    )
    if not description and not summary and not entries and not item.error:
        return None
    return {
        "kind": "subagent",
        "description": description,
        "subagentType": "explore",
        "mode": "foreground",
        **({"summary": summary} if summary else {}),
        **({"trace": entries} if entries else {}),
        **({"error": item.error} if item.error else {}),
    }


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
    if name == "web_search":
        value = arguments.get("query")
        return value if isinstance(value, str) else ""
    if name == "web_fetch":
        value = arguments.get("url")
        return value if isinstance(value, str) else ""
    if name == "memory_list":
        return "memory inventory"
    if name in ("memory_write", "memory_delete"):
        value = arguments.get("name")
        scope = arguments.get("scope", "workspace")
        return (
            f"{scope}:{value}"
            if isinstance(value, str)
            else "memory"
        )
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
        **(
            {"allowAlways": allow_always}
            if (allow_always := _approval_allow_always(request)) is not None
            else {}
        ),
    }


def _approval_allow_always(
    request: Any,
) -> dict[str, Any] | None:
    if request.tool_name in ("write_file", "edit_file"):
        return {
            "kind": "mode",
            "mode": "accept-write",
            "label": "Yes, allow all edits this session",
        }
    if request.tool_name != "run_command":
        return None
    effect = request.effect
    if (
        not isinstance(effect, ProcessExecEffect)
        or effect.classification == "destructive"
    ):
        return None
    command = request.arguments.get("command")
    if not isinstance(command, str):
        return None
    patterns = command_allow_patterns(command)
    if patterns is None:
        return None
    if len(patterns) == 1:
        label = (
            patterns[0][:-2]
            if patterns[0].endswith(":*")
            else patterns[0]
        )
        text = f'Yes, and don\'t ask again for "{label}" commands'
    else:
        text = (
            "Yes, and don't ask again for these "
            f"{len(patterns)} commands"
        )
    return {"kind": "command", "label": text}


def _tool_action(
    name: str,
) -> Literal[
    "Read",
    "Edit",
    "Run",
    "Skill",
    "Plan",
    "Subagent",
    "Browser",
    "Web",
    "Memory",
    "MCP",
]:
    if name in ("create_plan", "update_plan", "todo_write"):
        return "Plan"
    if name == "spawn_subagent":
        return "Subagent"
    if name == "browser_bridge":
        return "Browser"
    if name in ("web_search", "web_fetch"):
        return "Web"
    if name in (
        "memory_list",
        "memory_write",
        "memory_delete",
    ):
        return "Memory"
    if name.startswith("mcp_"):
        return "MCP"
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
    if name.startswith("mcp_"):
        if "__" in name:
            server, tool = name[4:].split("__", 1)
            return f"{server} / {tool}"
        management = {
            "mcp_list_servers": "server inventory",
            "mcp_reload": "reload manifests",
        }
        if name in management:
            return management[name]
        server_name = arguments.get("name")
        label = (
            server_name[:80]
            if isinstance(server_name, str) and server_name
            else name
        )
        verbs = {
            "mcp_add_server": "add",
            "mcp_test_server": "test",
            "mcp_authorize_server": "authorize",
            "mcp_remove_server": "remove",
            "mcp_revoke_server_auth": "revoke auth",
        }
        verb = verbs.get(name)
        if name == "mcp_set_enabled":
            enabled = arguments.get("enabled")
            verb = (
                "enable"
                if enabled is True
                else "disable"
                if enabled is False
                else "toggle"
            )
        if verb:
            return f"{verb} {label}"
        for key in ("url", "command"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return value[:80]
        return label
    if name == "memory_list":
        return "memory inventory"
    if name in ("memory_write", "memory_delete"):
        memory_name = arguments.get("name")
        scope = arguments.get("scope")
        if isinstance(memory_name, str) and memory_name:
            return (
                f"{scope}:{memory_name}"
                if isinstance(scope, str) and scope
                else memory_name
            )
        return "memory"
    if name == "web_search":
        query = arguments.get("query")
        return query[:80] if isinstance(query, str) else "search"
    if name == "web_fetch":
        url = arguments.get("url")
        return url[:80] if isinstance(url, str) else "fetch"
    if name == "browser_bridge":
        action = arguments.get("action")
        target = (
            arguments.get("url")
            or arguments.get("page_id")
            or arguments.get("browser_id")
            or ""
        )
        label = action if isinstance(action, str) and action else "browser"
        return (
            f"{label} {target}".strip()
            if isinstance(target, str)
            else label
        )
    if name == "spawn_subagent":
        value = arguments.get("description")
        if isinstance(value, str) and value:
            return value[:60]
    if name == "todo_write":
        todos = arguments.get("todos")
        if isinstance(todos, list):
            count = len(todos)
            return f"{count} todo" + ("" if count == 1 else "s")
    if name in ("create_plan", "update_plan"):
        plan = arguments.get("plan")
        if isinstance(plan, str):
            for line in plan.splitlines():
                text = line.strip().lstrip("#").strip()
                if text:
                    return text[:60]
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

"""SQLite persistence for committed harness events."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Sequence, TypeVar

from ling_ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from .approvals import ApprovalDecision, ApprovalKind, ApprovalRequest
from .attachments import UserAttachment
from .changes import RunChange
from .paths import default_state_root
from .selections import UserSelection
from .effects import (
    ApprovalMode,
    FilesystemReadEffect,
    FilesystemWriteEffect,
    McpAdminEffect,
    McpEffect,
    MetaEffect,
    NetworkEffect,
    ProcessExecEffect,
    SubagentEffect,
    UnknownEffect,
)
from .events import (
    ApprovalRequested,
    ApprovalResolved,
    AssistantMessageCommitted,
    BackgroundTaskNotice,
    BackgroundTasksCompleted,
    CompactionApplied,
    CommittedEvent,
    CommittedEventEnvelope,
    RunChangesCommitted,
    RunChangesReverted,
    RunStarted,
    RunStopped,
    ToolExecutionStarted,
    ToolResultCommitted,
    UsageUpdated,
    UserMessageCommitted,
)

SCHEMA_VERSION = 4
_T = TypeVar("_T")


class StorageError(RuntimeError):
    pass


class SessionNotFoundError(StorageError):
    pass


class SequenceConflictError(StorageError):
    pass


class DuplicateEventError(StorageError):
    pass


@dataclass(slots=True, frozen=True)
class SessionRecord:
    session_id: str
    workspace_root: Path
    title: str | None
    status: str
    created_at: int
    updated_at: int
    last_seq: int = 0
    approval_mode: ApprovalMode = ApprovalMode.MANUAL


BackgroundTaskStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]

TERMINAL_BACKGROUND_TASK_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)


BackgroundTaskKind = Literal["subagent", "command"]


@dataclass(slots=True, frozen=True)
class BackgroundTaskRecord:
    id: str
    session_id: str
    run_id: str
    tool_call_id: str
    description: str
    prompt: str
    status: BackgroundTaskStatus
    summary: str | None
    error: str | None
    trace: tuple[dict[str, Any], ...]
    created_at: int
    started_at: int | None = None
    completed_at: int | None = None
    continued_at: int | None = None
    kind: BackgroundTaskKind = "subagent"
    exit_code: int | None = None
    output_path: str | None = None
    pid: int | None = None


def default_storage_root() -> Path:
    return default_state_root()


def default_database_path(storage_root: str | Path | None = None) -> Path:
    root = Path(storage_root) if storage_root is not None else default_storage_root()
    return root / "sessions.db"


def _event_kind(event: CommittedEvent) -> str:
    kinds: dict[type[CommittedEvent], str] = {
        RunStarted: "run.started",
        UserMessageCommitted: "message.user.committed",
        AssistantMessageCommitted: "message.assistant.committed",
        ToolExecutionStarted: "tool.execution.started",
        ToolResultCommitted: "tool.result.committed",
        ApprovalRequested: "approval.requested",
        ApprovalResolved: "approval.resolved",
        CompactionApplied: "compaction.applied",
        RunChangesCommitted: "run.changes.committed",
        RunChangesReverted: "run.changes.reverted",
        UsageUpdated: "usage.updated",
        RunStopped: "run.stopped",
        BackgroundTasksCompleted: "background.tasks.completed",
    }
    try:
        return kinds[type(event)]
    except KeyError:
        raise StorageError(f"Unsupported committed event: {type(event)!r}") from None


def _json_default(value: Any) -> Any:
    if isinstance(value, asyncio.Event):
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object is not JSON serializable: {type(value)!r}")


def _serialize_event(event: CommittedEvent) -> str:
    if isinstance(event, ApprovalRequested):
        payload = {
            "approval_id": event.approval_id,
            "request": _approval_request_to_dict(event.request),
        }
    elif isinstance(event, ApprovalResolved):
        payload = {
            "approval_id": event.approval_id,
            "request": _approval_request_to_dict(event.request),
            "decision": event.decision.value,
        }
    else:
        payload = asdict(event)
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _approval_request_to_dict(request: ApprovalRequest) -> dict[str, Any]:
    return {
        "kind": request.kind.value,
        "tool_name": request.tool_name,
        "arguments": request.arguments,
        "reason": request.reason,
        "workspace_root": str(request.workspace_root),
        "call_id": request.call_id,
        "approval_id": request.approval_id,
        "run_id": request.run_id,
        "turn_id": request.turn_id,
        "effect": asdict(request.effect) if request.effect is not None else None,
        "effect_digest": request.effect_digest,
        "created_at": request.created_at,
    }


def _usage_from_dict(data: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(data.get("input_tokens", 0)),
        output_tokens=int(data.get("output_tokens", 0)),
        cache_read_tokens=int(data.get("cache_read_tokens", 0)),
        cache_write_tokens=int(data.get("cache_write_tokens", 0)),
        total_tokens=int(data.get("total_tokens", 0)),
        cost=float(data.get("cost", 0.0)),
    )


def _user_content_from_list(data: list[dict[str, Any]]) -> list[Any]:
    content: list[Any] = []
    for block in data:
        kind = block.get("type")
        if kind == "text":
            content.append(TextContent(block["text"]))
        elif kind == "image":
            content.append(
                ImageContent(
                    data=block["data"],
                    mime_type=block["mime_type"],
                )
            )
        else:
            raise StorageError(f"Unknown user content type: {kind!r}")
    return content


def _user_message_from_dict(data: dict[str, Any]) -> UserMessage:
    content = data.get("content", "")
    if isinstance(content, list):
        content = _user_content_from_list(content)
    return UserMessage(content=content, timestamp=int(data["timestamp"]))


def _user_attachment_from_dict(data: dict[str, Any]) -> UserAttachment:
    return UserAttachment(
        relative_path=data["relative_path"],
        name=data["name"],
        media_type=data["media_type"],
        kind=data.get("kind", "image"),
    )


def _user_selection_from_dict(data: dict[str, Any]) -> UserSelection:
    return UserSelection(
        kind=data["kind"],
        label=data["label"],
        text=data["text"],
        line_count=int(data.get("line_count", 1)),
        path=data.get("path"),
        start_line=data.get("start_line"),
        end_line=data.get("end_line"),
    )


def _assistant_content_from_list(data: list[dict[str, Any]]) -> list[Any]:
    content: list[Any] = []
    for block in data:
        kind = block.get("type")
        if kind == "text":
            content.append(TextContent(block["text"]))
        elif kind == "thinking":
            content.append(
                ThinkingContent(
                    thinking=block["thinking"],
                    signature=block.get("signature"),
                    redacted=bool(block.get("redacted", False)),
                )
            )
        elif kind == "tool_call":
            content.append(
                ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=block.get("arguments") or {},
                )
            )
        else:
            raise StorageError(f"Unknown assistant content type: {kind!r}")
    return content


def _assistant_message_from_dict(data: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        content=_assistant_content_from_list(data.get("content") or []),
        provider=data["provider"],
        model=data["model"],
        usage=_usage_from_dict(data.get("usage") or {}),
        stop_reason=data["stop_reason"],
        timestamp=int(data["timestamp"]),
        api=data.get("api", ""),
        error_message=data.get("error_message"),
    )


def _run_change_from_dict(data: dict[str, Any]) -> RunChange:
    return RunChange(
        path=str(data["path"]),
        before_exists=bool(data["before_exists"]),
        before=(
            str(data["before"])
            if data.get("before") is not None
            else None
        ),
        after_exists=bool(data["after_exists"]),
        after=(
            str(data["after"])
            if data.get("after") is not None
            else None
        ),
        added=int(data.get("added", 0)),
        removed=int(data.get("removed", 0)),
    )


def _tool_result_from_dict(data: dict[str, Any]) -> ToolResultMessage:
    content: list[Any] = []
    for block in data.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            content.append(TextContent(block["text"]))
        elif kind == "image":
            content.append(
                ImageContent(
                    data=block["data"],
                    mime_type=block["mime_type"],
                )
            )
        else:
            raise StorageError(f"Unknown tool result content type: {kind!r}")
    return ToolResultMessage(
        tool_call_id=data["tool_call_id"],
        tool_name=data["tool_name"],
        content=content,
        is_error=bool(data["is_error"]),
        timestamp=int(data["timestamp"]),
        details=data.get("details"),
    )


def _approval_request_from_dict(data: dict[str, Any]) -> ApprovalRequest:
    raw_effect = data.get("effect")
    return ApprovalRequest(
        kind=ApprovalKind(data["kind"]),
        tool_name=data["tool_name"],
        arguments=data.get("arguments") or {},
        reason=data["reason"],
        workspace_root=Path(data["workspace_root"]),
        call_id=data.get("call_id", ""),
        approval_id=data.get("approval_id", ""),
        run_id=data.get("run_id", ""),
        turn_id=data.get("turn_id", ""),
        effect=(
            _effect_from_dict(raw_effect)
            if isinstance(raw_effect, dict)
            else None
        ),
        effect_digest=data.get("effect_digest", ""),
        created_at=int(data.get("created_at", 0)),
    )


def _effect_from_dict(data: dict[str, Any]) -> Any:
    kind = data.get("kind")
    if kind == "filesystem_read":
        return FilesystemReadEffect(
            operation=data["operation"],
            path=Path(data["path"]),
            scope=data.get("scope", "workspace"),
        )
    if kind == "filesystem_write":
        return FilesystemWriteEffect(
            operation=data["operation"],
            path=Path(data["path"]),
        )
    if kind == "process_exec":
        return ProcessExecEffect(
            command=data["command"],
            cwd=Path(data["cwd"]),
            classification=data["classification"],
        )
    if kind == "network":
        return NetworkEffect(
            operation=data["operation"],
            target=data["target"],
            private_host=bool(data.get("private_host", False)),
        )
    if kind == "meta":
        return MetaEffect(operation=str(data.get("operation", "meta")))
    if kind == "subagent":
        return SubagentEffect()
    if kind == "mcp_call":
        return McpEffect(
            server=data["server"],
            tool=data["tool"],
            transport=data["transport"],
            target=data["target"],
            private_host=bool(data.get("private_host", False)),
            read_only=bool(data.get("read_only", False)),
            destructive=bool(data.get("destructive", False)),
            open_world=bool(data.get("open_world", False)),
            trusted_annotations=bool(
                data.get("trusted_annotations", False)
            ),
        )
    if kind == "mcp_admin":
        return McpAdminEffect(
            operation=str(data.get("operation", "admin")),
            server=(
                str(data["server"])
                if data.get("server") is not None
                else None
            ),
        )
    return UnknownEffect(note=str(data.get("note", "unknown effect")))


def _event_from_dict(kind: str, data: dict[str, Any]) -> CommittedEvent:
    if kind == "run.started":
        return RunStarted(continuation=bool(data.get("continuation", False)))
    if kind == "message.user.committed":
        return UserMessageCommitted(
            message=_user_message_from_dict(data["message"]),
            attachments=tuple(
                _user_attachment_from_dict(item)
                for item in data.get("attachments") or ()
            ),
            selections=tuple(
                _user_selection_from_dict(item)
                for item in data.get("selections") or ()
            ),
            display_text=data.get("display_text"),
        )
    if kind == "message.assistant.committed":
        return AssistantMessageCommitted(
            message=_assistant_message_from_dict(data["message"])
        )
    if kind == "tool.execution.started":
        call = data["call"]
        return ToolExecutionStarted(
            call=ToolCall(
                id=call["id"],
                name=call["name"],
                arguments=call.get("arguments") or {},
            )
        )
    if kind == "tool.result.committed":
        return ToolResultCommitted(result=_tool_result_from_dict(data["result"]))
    if kind == "approval.requested":
        return ApprovalRequested(
            approval_id=data["approval_id"],
            request=_approval_request_from_dict(data["request"]),
        )
    if kind == "approval.resolved":
        return ApprovalResolved(
            approval_id=data["approval_id"],
            request=_approval_request_from_dict(data["request"]),
            decision=ApprovalDecision(data["decision"]),
        )
    if kind == "compaction.applied":
        return CompactionApplied(
            compaction_id=data["compaction_id"],
            through_message_id=data["through_message_id"],
            summary=_user_message_from_dict(data["summary"]),
            replaced_message_ids=tuple(
                data.get("replaced_message_ids") or ()
            ),
            replaced_count=int(data.get("replaced_count", 0)),
            estimated_tokens=int(data.get("estimated_tokens", 0)),
        )
    if kind == "run.changes.committed":
        return RunChangesCommitted(
            changes=tuple(
                _run_change_from_dict(item)
                for item in data.get("changes") or ()
            )
        )
    if kind == "run.changes.reverted":
        return RunChangesReverted()
    if kind == "usage.updated":
        return UsageUpdated(usage=_usage_from_dict(data["usage"]))
    if kind == "run.stopped":
        raw_message = data.get("message")
        return RunStopped(
            status=data["status"],
            message=(
                _assistant_message_from_dict(raw_message)
                if raw_message is not None
                else None
            ),
        )
    if kind == "background.tasks.completed":
        return BackgroundTasksCompleted(
            tasks=tuple(
                BackgroundTaskNotice(
                    id=str(item["id"]),
                    description=str(item["description"]),
                    status=item["status"],
                    summary=item.get("summary"),
                    error=item.get("error"),
                    kind=(
                        item.get("kind")
                        if item.get("kind") in ("subagent", "command")
                        else "subagent"
                    ),
                    exit_code=item.get("exit_code"),
                    log_path=item.get("log_path"),
                )
                for item in data.get("tasks") or ()
            )
        )
    raise StorageError(f"Unknown committed event kind: {kind!r}")


def _session_status_for_event(current: str, event: CommittedEvent) -> str:
    if isinstance(event, RunStarted):
        return "running"
    if isinstance(event, RunStopped):
        if event.status in ("error", "max_steps"):
            return "error"
        return "idle"
    return current


class SQLiteSessionStore:
    """SQLite-backed committed event store."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._path = (
            Path(database_path)
            if database_path is not None
            else default_database_path()
        )
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection = sqlite3.connect(
            str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self._connection.execute("PRAGMA foreign_keys=ON")
        if str(self._path) != ":memory:":
            self._connection.execute("PRAGMA journal_mode=WAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                workspace_root TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_seq INTEGER NOT NULL,
                approval_mode TEXT NOT NULL DEFAULT 'manual'
            );
            CREATE TABLE IF NOT EXISTS events (
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                kind TEXT NOT NULL,
                emitted_at INTEGER NOT NULL,
                message_id TEXT,
                call_id TEXT,
                approval_id TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (session_id, seq),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            CREATE INDEX IF NOT EXISTS events_session_seq
                ON events(session_id, seq);
            CREATE TABLE IF NOT EXISTS background_tasks (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                tool_call_id TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                error TEXT,
                trace_json TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                continued_at INTEGER,
                kind TEXT NOT NULL DEFAULT 'subagent',
                exit_code INTEGER,
                output_path TEXT,
                pid INTEGER,
                FOREIGN KEY (session_id)
                    REFERENCES sessions(session_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS background_tasks_session_status
                ON background_tasks(session_id, status);
            CREATE INDEX IF NOT EXISTS background_tasks_session_continued
                ON background_tasks(session_id, continued_at);
            """
        )
        row = self._connection.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO schema_version(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        else:
            version = int(row["version"])
            if version == 1:
                columns = {
                    str(column["name"])
                    for column in self._connection.execute(
                        "PRAGMA table_info(sessions)"
                    )
                }
                if "approval_mode" not in columns:
                    self._connection.execute(
                        "ALTER TABLE sessions ADD COLUMN "
                        "approval_mode TEXT NOT NULL DEFAULT 'manual'"
                    )
                version = 2
                self._connection.execute(
                    "UPDATE schema_version SET version = ?",
                    (version,),
                )
            if version == 2:
                version = 3
                self._connection.execute(
                    "UPDATE schema_version SET version = ?",
                    (version,),
                )
            if version == 3:
                columns = {
                    str(column["name"])
                    for column in self._connection.execute(
                        "PRAGMA table_info(background_tasks)"
                    )
                }
                additions = {
                    "kind": "TEXT NOT NULL DEFAULT 'subagent'",
                    "exit_code": "INTEGER",
                    "output_path": "TEXT",
                    "pid": "INTEGER",
                }
                for name, definition in additions.items():
                    if name not in columns:
                        self._connection.execute(
                            f"ALTER TABLE background_tasks ADD COLUMN "
                            f"{name} {definition}"
                        )
                version = 4
                self._connection.execute(
                    "UPDATE schema_version SET version = ?",
                    (version,),
                )
            if version != SCHEMA_VERSION:
                raise StorageError(
                    f"Unsupported schema version: {row['version']}"
                )

    async def _run(self, function: Any, *args: Any) -> Any:
        async with self._lock:
            try:
                return await asyncio.to_thread(function, *args)
            except sqlite3.Error as exc:
                raise StorageError(str(exc)) from exc

    async def upsert_session(self, session: SessionRecord) -> None:
        await self._run(self._upsert_session_sync, session)

    def _upsert_session_sync(self, session: SessionRecord) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """
                INSERT INTO sessions(
                    session_id, workspace_root, title, status,
                    created_at, updated_at, last_seq, approval_mode
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace_root=excluded.workspace_root,
                    title=excluded.title,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    approval_mode=excluded.approval_mode
                """,
                (
                    session.session_id,
                    str(session.workspace_root),
                    session.title,
                    session.status,
                    session.created_at,
                    session.updated_at,
                    session.approval_mode.value,
                ),
            )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await self._run(self._get_session_sync, session_id)

    def _get_session_sync(self, session_id: str) -> SessionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return self._session_from_row(row) if row is not None else None

    async def list_sessions(self) -> list[SessionRecord]:
        return await self._run(self._list_sessions_sync)

    async def delete_session(self, session_id: str) -> bool:
        return await self._run(self._delete_session_sync, session_id)

    def _delete_session_sync(self, session_id: str) -> bool:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "DELETE FROM events WHERE session_id = ?",
                (session_id,),
            )
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            self._connection.execute("COMMIT")
            return cursor.rowcount > 0
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    async def delete_events(
        self,
        session_id: str,
        seqs: tuple[int, ...],
    ) -> int:
        return await self._run(
            self._delete_events_sync,
            session_id,
            seqs,
        )

    def _delete_events_sync(
        self,
        session_id: str,
        seqs: tuple[int, ...],
    ) -> int:
        if not seqs:
            return 0
        placeholders = ",".join("?" for _ in seqs)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                f"DELETE FROM events WHERE session_id = ? "
                f"AND seq IN ({placeholders})",
                (session_id, *seqs),
            )
            self._connection.execute(
                "UPDATE sessions SET last_seq = ("
                "SELECT COALESCE(MAX(seq), 0) FROM events "
                "WHERE session_id = ?"
                "), updated_at = ? WHERE session_id = ?",
                (session_id, int(time.time() * 1000), session_id),
            )
            self._connection.execute("COMMIT")
            return cursor.rowcount
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    async def delete_events_after(
        self,
        session_id: str,
        seq: int,
    ) -> int:
        return await self._run(
            self._delete_events_after_sync,
            session_id,
            seq,
        )

    def _delete_events_after_sync(
        self,
        session_id: str,
        seq: int,
    ) -> int:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                "DELETE FROM events WHERE session_id = ? AND seq > ?",
                (session_id, seq),
            )
            self._connection.execute(
                "UPDATE sessions SET last_seq = ("
                "SELECT COALESCE(MAX(seq), 0) FROM events "
                "WHERE session_id = ?"
                "), updated_at = ? WHERE session_id = ?",
                (session_id, int(time.time() * 1000), session_id),
            )
            self._connection.execute("COMMIT")
            return cursor.rowcount
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    async def recover_interrupted_approvals(self) -> int:
        recovered = 0
        for session in await self.list_sessions():
            if session.status != "running":
                continue
            events = await self.load_committed(session.session_id)
            resolved = {
                envelope.event.approval_id
                for envelope in events
                if isinstance(envelope.event, ApprovalResolved)
            }
            pending = [
                envelope
                for envelope in events
                if isinstance(envelope.event, ApprovalRequested)
                and envelope.event.approval_id not in resolved
            ]
            for requested in pending:
                event = requested.event
                assert isinstance(event, ApprovalRequested)
                await self.append_committed(
                    CommittedEventEnvelope(
                        session_id=session.session_id,
                        seq=await self.last_seq(session.session_id) + 1,
                        event_id=f"recovery-{uuid.uuid4()}",
                        run_id=requested.run_id,
                        turn_id=requested.turn_id,
                        step=requested.step,
                        event=ApprovalResolved(
                            approval_id=event.approval_id,
                            request=event.request,
                            decision=ApprovalDecision.DENY,
                        ),
                        emitted_at=int(time.time() * 1000),
                        call_id=requested.call_id,
                        approval_id=event.approval_id,
                    )
                )
                recovered += 1

            last = events[-1] if events else None
            await self.append_committed(
                CommittedEventEnvelope(
                    session_id=session.session_id,
                    seq=await self.last_seq(session.session_id) + 1,
                    event_id=f"recovery-{uuid.uuid4()}",
                    run_id=last.run_id if last is not None else "recovery",
                    turn_id=last.turn_id if last is not None else "recovery",
                    step=last.step if last is not None else 0,
                    event=RunStopped(status="aborted", message=None),
                    emitted_at=int(time.time() * 1000),
                )
            )
        return recovered

    def _list_sessions_sync(self) -> list[SessionRecord]:
        rows = self._connection.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [self._session_from_row(row) for row in rows]

    async def set_approval_mode(
        self,
        session_id: str,
        mode: ApprovalMode,
        *,
        updated_at: int,
    ) -> None:
        await self._run(
            self._set_approval_mode_sync,
            session_id,
            mode,
            updated_at,
        )

    def _set_approval_mode_sync(
        self,
        session_id: str,
        mode: ApprovalMode,
        updated_at: int,
    ) -> None:
        cursor = self._connection.execute(
            "UPDATE sessions SET approval_mode = ?, updated_at = ? "
            "WHERE session_id = ?",
            (mode.value, updated_at, session_id),
        )
        if cursor.rowcount == 0:
            raise SessionNotFoundError(f"Unknown session: {session_id}")

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            workspace_root=Path(row["workspace_root"]),
            title=row["title"],
            status=row["status"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            last_seq=int(row["last_seq"]),
            approval_mode=ApprovalMode(
                row["approval_mode"] if "approval_mode" in row.keys() else "manual"
            ),
        )

    @staticmethod
    def _background_task_from_row(row: sqlite3.Row) -> BackgroundTaskRecord:
        try:
            raw_trace = json.loads(row["trace_json"] or "[]")
        except (TypeError, ValueError):
            raw_trace = []
        trace = tuple(
            item
            for item in raw_trace
            if isinstance(item, dict)
        ) if isinstance(raw_trace, list) else ()
        return BackgroundTaskRecord(
            id=row["id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            tool_call_id=row["tool_call_id"],
            description=row["description"],
            prompt=row["prompt"],
            status=row["status"],
            summary=row["summary"],
            error=row["error"],
            trace=trace,
            created_at=int(row["created_at"]),
            started_at=(
                int(row["started_at"])
                if row["started_at"] is not None
                else None
            ),
            completed_at=(
                int(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            continued_at=(
                int(row["continued_at"])
                if row["continued_at"] is not None
                else None
            ),
            kind=row["kind"] if row["kind"] in ("subagent", "command") else "subagent",
            exit_code=(
                int(row["exit_code"])
                if row["exit_code"] is not None
                else None
            ),
            output_path=row["output_path"],
            pid=int(row["pid"]) if row["pid"] is not None else None,
        )

    async def create_background_task(
        self,
        task: BackgroundTaskRecord,
    ) -> None:
        await self._run(self._create_background_task_sync, task)

    def _create_background_task_sync(
        self,
        task: BackgroundTaskRecord,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO background_tasks(
                id, session_id, run_id, tool_call_id, description,
                prompt, status, summary, error, trace_json,
                created_at, started_at, completed_at, continued_at,
                kind, exit_code, output_path, pid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.session_id,
                task.run_id,
                task.tool_call_id,
                task.description,
                task.prompt,
                task.status,
                task.summary,
                task.error,
                json.dumps(
                    list(task.trace),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                task.created_at,
                task.started_at,
                task.completed_at,
                task.continued_at,
                task.kind,
                task.exit_code,
                task.output_path,
                task.pid,
            ),
        )

    async def get_background_task(
        self,
        task_id: str,
    ) -> BackgroundTaskRecord | None:
        return await self._run(self._get_background_task_sync, task_id)

    def _get_background_task_sync(
        self,
        task_id: str,
    ) -> BackgroundTaskRecord | None:
        row = self._connection.execute(
            "SELECT * FROM background_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return (
            self._background_task_from_row(row)
            if row is not None
            else None
        )

    async def list_background_tasks(
        self,
        session_id: str,
    ) -> list[BackgroundTaskRecord]:
        return await self._run(
            self._list_background_tasks_sync,
            session_id,
        )

    def _list_background_tasks_sync(
        self,
        session_id: str,
    ) -> list[BackgroundTaskRecord]:
        rows = self._connection.execute(
            """
            SELECT * FROM background_tasks
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        return [self._background_task_from_row(row) for row in rows]

    async def mark_background_task_running(
        self,
        task_id: str,
        *,
        started_at: int,
    ) -> BackgroundTaskRecord | None:
        await self._run(
            self._mark_background_task_running_sync,
            task_id,
            started_at,
        )
        return await self.get_background_task(task_id)

    def _mark_background_task_running_sync(
        self,
        task_id: str,
        started_at: int,
    ) -> None:
        self._connection.execute(
            """
            UPDATE background_tasks
            SET status = 'running', started_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (started_at, task_id),
        )

    async def finish_background_task(
        self,
        task_id: str,
        *,
        status: BackgroundTaskStatus,
        completed_at: int,
        summary: str | None = None,
        error: str | None = None,
        trace: tuple[dict[str, Any], ...] | None = None,
        exit_code: int | None = None,
    ) -> BackgroundTaskRecord | None:
        await self._run(
            self._finish_background_task_sync,
            task_id,
            status,
            completed_at,
            summary,
            error,
            trace,
            exit_code,
        )
        return await self.get_background_task(task_id)

    def _finish_background_task_sync(
        self,
        task_id: str,
        status: BackgroundTaskStatus,
        completed_at: int,
        summary: str | None,
        error: str | None,
        trace: tuple[dict[str, Any], ...] | None,
        exit_code: int | None,
    ) -> None:
        values: list[Any] = [
            status,
            summary,
            error,
            completed_at,
            exit_code,
        ]
        trace_sql = ""
        if trace is not None:
            trace_sql = ", trace_json = ?"
            values.append(
                json.dumps(
                    list(trace),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        values.append(task_id)
        self._connection.execute(
            f"""
            UPDATE background_tasks
            SET status = ?, summary = ?, error = ?, completed_at = ?,
                exit_code = ?
                {trace_sql}
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            tuple(values),
        )

    async def claim_background_tasks(
        self,
        session_id: str,
        *,
        claimed_at: int,
    ) -> list[BackgroundTaskRecord]:
        return await self._run(
            self._claim_background_tasks_sync,
            session_id,
            claimed_at,
        )

    def _claim_background_tasks_sync(
        self,
        session_id: str,
        claimed_at: int,
    ) -> list[BackgroundTaskRecord]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._connection.execute(
                """
                SELECT * FROM background_tasks
                WHERE session_id = ?
                  AND status IN ('completed', 'failed', 'interrupted')
                  AND continued_at IS NULL
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
            tasks = [self._background_task_from_row(row) for row in rows]
            if tasks:
                self._connection.executemany(
                    """
                    UPDATE background_tasks
                    SET continued_at = ?
                    WHERE id = ? AND continued_at IS NULL
                    """,
                    [(claimed_at, task.id) for task in tasks],
                )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        return tasks

    async def release_background_tasks(
        self,
        task_ids: Sequence[str],
    ) -> None:
        await self._run(self._release_background_tasks_sync, task_ids)

    def _release_background_tasks_sync(
        self,
        task_ids: Sequence[str],
    ) -> None:
        if not task_ids:
            return
        placeholders = ",".join("?" for _ in task_ids)
        self._connection.execute(
            f"""
            UPDATE background_tasks
            SET continued_at = NULL
            WHERE id IN ({placeholders})
            """,
            tuple(task_ids),
        )

    async def recover_background_tasks(
        self,
        *,
        completed_at: int,
    ) -> list[BackgroundTaskRecord]:
        return await self._run(
            self._recover_background_tasks_sync,
            completed_at,
        )

    def _recover_background_tasks_sync(
        self,
        completed_at: int,
    ) -> list[BackgroundTaskRecord]:
        rows = self._connection.execute(
            """
            SELECT * FROM background_tasks
            WHERE status IN ('pending', 'running')
            """
        ).fetchall()
        tasks = [self._background_task_from_row(row) for row in rows]
        self._connection.execute(
            """
            UPDATE background_tasks
            SET status = 'interrupted', completed_at = ?,
                error = CASE
                    WHEN kind = 'command' AND pid IS NOT NULL
                         AND (error IS NULL OR error = '')
                    THEN 'The sidecar stopped while this command was '
                         || 'running; the process may still be running '
                         || '(PID ' || pid || ').'
                    ELSE error
                END
            WHERE status IN ('pending', 'running')
            """,
            (completed_at,),
        )
        recovered: list[BackgroundTaskRecord] = []
        for task in tasks:
            error = task.error
            if task.kind == "command" and task.pid is not None and not error:
                error = (
                    "The sidecar stopped while this command was running; "
                    f"the process may still be running (PID {task.pid})."
                )
            recovered.append(
                replace(
                    task,
                    status="interrupted",
                    completed_at=completed_at,
                    error=error,
                )
            )
        return recovered

    async def append_committed(self, envelope: CommittedEventEnvelope) -> None:
        await self._run(self._append_committed_sync, envelope)

    def _append_committed_sync(self, envelope: CommittedEventEnvelope) -> None:
        kind = _event_kind(envelope.event)
        payload_json = _serialize_event(envelope.event)

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            session_row = self._connection.execute(
                "SELECT last_seq, status FROM sessions WHERE session_id = ?",
                (envelope.session_id,),
            ).fetchone()
            if session_row is None:
                raise SessionNotFoundError(
                    f"Unknown session: {envelope.session_id}"
                )

            expected_seq = int(session_row["last_seq"]) + 1
            if envelope.seq != expected_seq:
                raise SequenceConflictError(
                    f"Expected seq {expected_seq}, got {envelope.seq}"
                )

            try:
                self._connection.execute(
                    """
                    INSERT INTO events(
                        session_id, seq, event_id, run_id, turn_id, step,
                        kind, emitted_at, message_id, call_id, approval_id,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope.session_id,
                        envelope.seq,
                        envelope.event_id,
                        envelope.run_id,
                        envelope.turn_id,
                        envelope.step,
                        kind,
                        envelope.emitted_at,
                        envelope.message_id,
                        envelope.call_id,
                        envelope.approval_id,
                        payload_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "event_id" in str(exc):
                    raise DuplicateEventError(
                        f"Duplicate event_id: {envelope.event_id}"
                    ) from exc
                raise

            next_status = _session_status_for_event(
                str(session_row["status"]),
                envelope.event,
            )
            self._connection.execute(
                """
                UPDATE sessions
                SET last_seq = ?, updated_at = ?, status = ?
                WHERE session_id = ?
                """,
                (
                    envelope.seq,
                    envelope.emitted_at,
                    next_status,
                    envelope.session_id,
                ),
            )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    async def last_seq(self, session_id: str) -> int:
        session = await self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"Unknown session: {session_id}")
        return session.last_seq

    async def load_committed(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
    ) -> list[CommittedEventEnvelope]:
        if after_seq < 0:
            raise ValueError("after_seq must not be negative")
        return await self._run(
            self._load_committed_sync,
            session_id,
            after_seq,
        )

    def _load_committed_sync(
        self,
        session_id: str,
        after_seq: int,
    ) -> list[CommittedEventEnvelope]:
        session = self._get_session_sync(session_id)
        if session is None:
            raise SessionNotFoundError(f"Unknown session: {session_id}")
        rows = self._connection.execute(
            """
            SELECT * FROM events
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (session_id, after_seq),
        ).fetchall()
        envelopes: list[CommittedEventEnvelope] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            event = _event_from_dict(str(row["kind"]), payload)
            envelopes.append(
                CommittedEventEnvelope(
                    session_id=row["session_id"],
                    seq=int(row["seq"]),
                    event_id=row["event_id"],
                    run_id=row["run_id"],
                    turn_id=row["turn_id"],
                    step=int(row["step"]),
                    event=event,
                    emitted_at=int(row["emitted_at"]),
                    message_id=row["message_id"],
                    call_id=row["call_id"],
                    approval_id=row["approval_id"],
                )
            )
        return envelopes

    async def close(self) -> None:
        await self._run(self._connection.close)

    async def __aenter__(self) -> "SQLiteSessionStore":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()


__all__ = [
    "DuplicateEventError",
    "SQLiteSessionStore",
    "SequenceConflictError",
    "SessionNotFoundError",
    "SessionRecord",
    "StorageError",
    "default_database_path",
    "default_storage_root",
]

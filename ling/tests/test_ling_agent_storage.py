"""Tests for the SQLite committed-event store."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
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
    ApprovalMode,
    ApprovalRequest,
    ApprovalRequested,
    ApprovalResolved,
    AssistantMessageCommitted,
    CommittedEventEnvelope,
    DuplicateEventError,
    RunChange,
    RunChangesCommitted,
    RunChangesReverted,
    RunStarted,
    RunStopped,
    SQLiteSessionStore,
    SequenceConflictError,
    SessionNotFoundError,
    SessionRecord,
    ToolExecutionStarted,
    ToolResultCommitted,
    UserMessageCommitted,
)
from ling_agent.effects import FilesystemWriteEffect
from ling_agent.events import BackgroundTasksCompleted
from ling_agent.storage import BackgroundTaskRecord, _event_from_dict


def _envelope(
    seq: int,
    event: object,
    *,
    event_id: str | None = None,
    message_id: str | None = None,
    call_id: str | None = None,
    approval_id: str | None = None,
) -> CommittedEventEnvelope:
    return CommittedEventEnvelope(
        session_id="session-1",
        seq=seq,
        event_id=event_id or f"event-{seq}",
        run_id="run-1",
        turn_id="turn-1",
        step=1 if seq > 2 else 0,
        event=event,
        emitted_at=seq,
        message_id=message_id,
        call_id=call_id,
        approval_id=approval_id,
    )


async def _round_trip(database_path: Path, workspace: Path) -> None:
    store = SQLiteSessionStore(database_path)
    session = SessionRecord(
        session_id="session-1",
        workspace_root=workspace,
        title="Test session",
        status="idle",
        created_at=1,
        updated_at=1,
        approval_mode=ApprovalMode.AUTO,
    )
    await store.upsert_session(session)

    call = ToolCall(id="call-1", name="write_file", arguments={"path": "a.txt"})
    user = UserMessage(content="hello", timestamp=1)
    assistant = AssistantMessage(
        content=[TextContent("working"), call],
        provider="test",
        model="test-model",
        api="openai-completions",
        usage=Usage(input_tokens=2, output_tokens=3, total_tokens=5),
        stop_reason="tool_use",
        timestamp=2,
    )
    tool_result = ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=[TextContent("written")],
        is_error=False,
        timestamp=3,
    )
    approval = ApprovalRequest(
        kind=ApprovalKind.WRITE,
        tool_name="write_file",
        arguments={"path": "a.txt"},
        reason="Write requires approval",
        workspace_root=workspace,
        call_id=call.id,
        approval_id="approval-1",
        effect=FilesystemWriteEffect(
            operation="write",
            path=workspace / "a.txt",
        ),
        effect_digest="digest-1",
    )

    envelopes = [
        _envelope(1, RunStarted()),
        _envelope(
            2,
            UserMessageCommitted(message=user),
            message_id="message-user-1",
        ),
        _envelope(
            3,
            AssistantMessageCommitted(message=assistant),
            message_id="message-assistant-1",
        ),
        _envelope(
            4,
            ApprovalRequested(approval_id="approval-1", request=approval),
            call_id=call.id,
            approval_id="approval-1",
        ),
        _envelope(
            5,
            ApprovalResolved(
                approval_id="approval-1",
                request=approval,
                decision=ApprovalDecision.ALLOW_ONCE,
            ),
            call_id=call.id,
            approval_id="approval-1",
        ),
        _envelope(6, ToolExecutionStarted(call=call), call_id=call.id),
        _envelope(7, ToolResultCommitted(result=tool_result), call_id=call.id),
        _envelope(
            8,
            RunStopped(status="completed", message=assistant),
            message_id="message-assistant-1",
        ),
    ]
    for envelope in envelopes:
        await store.append_committed(envelope)

    assert await store.last_seq("session-1") == 8
    tail = await store.load_committed("session-1", after_seq=3)
    assert [envelope.seq for envelope in tail] == [4, 5, 6, 7, 8]
    assert isinstance(tail[0].event, ApprovalRequested)
    assert tail[0].event.request.kind is ApprovalKind.WRITE
    assert isinstance(tail[1].event, ApprovalResolved)
    assert tail[1].event.decision is ApprovalDecision.ALLOW_ONCE
    assert isinstance(tail[2].event, ToolExecutionStarted)
    assert tail[2].event.call.arguments == {"path": "a.txt"}
    assert isinstance(tail[3].event, ToolResultCommitted)
    assert tail[3].event.result.content == [TextContent("written")]

    loaded_session = await store.get_session("session-1")
    assert loaded_session is not None
    assert loaded_session.last_seq == 8
    assert loaded_session.approval_mode is ApprovalMode.AUTO
    assert loaded_session.status == "idle"
    await store.close()

    reopened = SQLiteSessionStore(database_path)
    persisted = await reopened.load_committed("session-1")
    assert len(persisted) == 8
    assert isinstance(persisted[2].event, AssistantMessageCommitted)
    assert persisted[2].event.message.content[1] == call
    assert persisted[3].event.request.effect == FilesystemWriteEffect(
        operation="write",
        path=workspace / "a.txt",
    )
    assert [session.session_id for session in await reopened.list_sessions()] == [
        "session-1"
    ]
    await reopened.close()

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()


def test_sqlite_round_trip_and_wal() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(_round_trip(root / "sessions.db", root))


async def _changes_round_trip(
    database_path: Path,
    workspace: Path,
) -> None:
    store = SQLiteSessionStore(database_path)
    await store.upsert_session(
        SessionRecord(
            session_id="session-1",
            workspace_root=workspace,
            title="Changes",
            status="idle",
            created_at=1,
            updated_at=1,
        )
    )
    change = RunChange(
        path="a.txt",
        before_exists=True,
        before="old\n",
        after_exists=True,
        after="new\n",
        added=1,
        removed=1,
    )
    await store.append_committed(_envelope(1, RunStarted()))
    await store.append_committed(
        _envelope(2, RunChangesCommitted(changes=(change,)))
    )
    await store.append_committed(_envelope(3, RunChangesReverted()))
    await store.close()

    reopened = SQLiteSessionStore(database_path)
    events = await reopened.load_committed("session-1")
    committed = events[1].event
    assert isinstance(committed, RunChangesCommitted)
    assert committed.changes == (change,)
    assert isinstance(events[2].event, RunChangesReverted)
    await reopened.close()


def test_run_changes_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(_changes_round_trip(root / "sessions.db", root))


async def _conflicts(database_path: Path, workspace: Path) -> None:
    store = SQLiteSessionStore(database_path)
    await store.upsert_session(
        SessionRecord(
            session_id="session-1",
            workspace_root=workspace,
            title=None,
            status="idle",
            created_at=1,
            updated_at=1,
        )
    )
    await store.append_committed(_envelope(1, RunStarted()))

    try:
        await store.append_committed(_envelope(3, RunStarted()))
    except SequenceConflictError:
        pass
    else:
        raise AssertionError("Expected sequence conflict")
    assert await store.last_seq("session-1") == 1
    assert len(await store.load_committed("session-1")) == 1

    try:
        await store.append_committed(
            _envelope(2, RunStarted(), event_id="event-1")
        )
    except DuplicateEventError:
        pass
    else:
        raise AssertionError("Expected duplicate event rejection")
    assert await store.last_seq("session-1") == 1

    try:
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="missing",
                seq=1,
                event_id="missing-event",
                run_id="run-1",
                turn_id="turn-1",
                step=0,
                event=RunStarted(),
                emitted_at=1,
            )
        )
    except SessionNotFoundError:
        pass
    else:
        raise AssertionError("Expected missing session rejection")
    await store.close()


def test_sqlite_conflicts_rollback() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(_conflicts(root / "sessions.db", root))


async def _schema_v1_migration(database_path: Path, workspace: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES (1);
            CREATE TABLE sessions(
                session_id TEXT PRIMARY KEY,
                workspace_root TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_seq INTEGER NOT NULL
            );
            CREATE TABLE events(
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
                PRIMARY KEY (session_id, seq)
            );
            """
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("old-session", str(workspace), "old", "idle", 1, 1, 0),
        )
        connection.commit()
    finally:
        connection.close()

    store = SQLiteSessionStore(database_path)
    loaded = await store.get_session("old-session")
    assert loaded is not None
    assert loaded.approval_mode is ApprovalMode.MANUAL
    await store.set_approval_mode(
        "old-session",
        ApprovalMode.ACCEPT_WRITE,
        updated_at=2,
    )
    await store.close()

    reopened = SQLiteSessionStore(database_path)
    migrated = await reopened.get_session("old-session")
    assert migrated is not None
    assert migrated.approval_mode is ApprovalMode.ACCEPT_WRITE
    await reopened.close()

    connection = sqlite3.connect(database_path)
    try:
        version = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        assert version == 4
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "background_tasks" in tables
    finally:
        connection.close()


def test_sqlite_schema_v1_migration() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(_schema_v1_migration(root / "sessions.db", root))


async def _recover_interrupted(database_path: Path, workspace: Path) -> None:
    store = SQLiteSessionStore(database_path)
    await store.upsert_session(
        SessionRecord(
            session_id="session-1",
            workspace_root=workspace,
            title="interrupted",
            status="running",
            created_at=1,
            updated_at=1,
        )
    )
    request = ApprovalRequest(
        kind=ApprovalKind.WRITE,
        tool_name="write_file",
        arguments={"path": "a.txt"},
        reason="write",
        workspace_root=workspace,
        call_id="call-1",
        approval_id="approval-1",
    )
    await store.append_committed(
        _envelope(
            1,
            ApprovalRequested(approval_id="approval-1", request=request),
            call_id="call-1",
            approval_id="approval-1",
        )
    )
    await store.close()

    reopened = SQLiteSessionStore(database_path)
    recovered = await reopened.recover_interrupted_approvals()
    assert recovered == 1
    events = await reopened.load_committed("session-1")
    assert isinstance(events[-2].event, ApprovalResolved)
    assert events[-2].event.decision is ApprovalDecision.DENY
    assert isinstance(events[-1].event, RunStopped)
    assert events[-1].event.status == "aborted"
    session = await reopened.get_session("session-1")
    assert session is not None and session.status == "idle"
    await reopened.close()


def test_sqlite_recovers_interrupted_approvals() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(_recover_interrupted(root / "sessions.db", root))


async def _delete_session(database_path: Path, workspace: Path) -> None:
    store = SQLiteSessionStore(database_path)
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-1",
                workspace_root=workspace,
                title="Delete me",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-1",
                seq=1,
                event_id="event-1",
                run_id="run-1",
                turn_id="turn-1",
                step=0,
                event=RunStarted(),
            )
        )
        await store.create_background_task(
            BackgroundTaskRecord(
                id="task-1",
                session_id="session-1",
                run_id="run-1",
                tool_call_id="call-1",
                description="inspect auth",
                prompt="inspect auth",
                status="completed",
                summary="auth uses JWT",
                error=None,
                trace=(),
                created_at=1,
                completed_at=2,
            )
        )

        assert await store.delete_session("session-1") is True
        assert await store.get_background_task("task-1") is None
        assert await store.get_session("session-1") is None
        try:
            await store.load_committed("session-1")
        except SessionNotFoundError:
            pass
        else:
            raise AssertionError("Expected deleted session to be missing")
        assert await store.delete_session("session-1") is False
    finally:
        await store.close()


def test_sqlite_deletes_session_and_events() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(_delete_session(root / "sessions.db", root))


async def _schema_v3_migration(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES (3);
            CREATE TABLE background_tasks (
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
                continued_at INTEGER
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    store = SQLiteSessionStore(database_path)
    await store.close()

    connection = sqlite3.connect(database_path)
    try:
        version = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        assert version == 4
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(background_tasks)"
            )
        }
        assert {"kind", "exit_code", "output_path", "pid"} <= columns
    finally:
        connection.close()


def test_sqlite_schema_v3_migration() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_schema_v3_migration(Path(temp) / "sessions.db"))


async def _command_task_round_trip(
    database_path: Path,
    workspace: Path,
) -> None:
    store = SQLiteSessionStore(database_path)
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-1",
                workspace_root=workspace,
                title="Command",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        await store.create_background_task(
            BackgroundTaskRecord(
                id="task-command",
                session_id="session-1",
                run_id="run-1",
                tool_call_id="call-1",
                description="npm run build",
                prompt="npm run build",
                status="running",
                summary=None,
                error=None,
                trace=(),
                created_at=1,
                started_at=2,
                kind="command",
                output_path=str(workspace / ".agents" / "background" / "task-command.log"),
                pid=1234,
            )
        )
        task = await store.get_background_task("task-command")
        assert task is not None
        assert task.kind == "command"
        assert task.pid == 1234
        assert task.output_path is not None

        failed = await store.finish_background_task(
            "task-command",
            status="failed",
            completed_at=3,
            summary="build failed",
            exit_code=2,
        )
        assert failed is not None
        assert failed.status == "failed"
        assert failed.exit_code == 2

        late = await store.finish_background_task(
            "task-command",
            status="completed",
            completed_at=4,
            summary="late",
            exit_code=0,
        )
        assert late is not None
        assert late.status == "failed"
        assert late.exit_code == 2
    finally:
        await store.close()


def test_sqlite_command_task_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        asyncio.run(
            _command_task_round_trip(
                root / "sessions.db",
                root / "workspace",
            )
        )


def test_old_background_event_uses_defaults() -> None:
    event = _event_from_dict(
        "background.tasks.completed",
        {
            "tasks": [
                {
                    "id": "task-old",
                    "description": "old task",
                    "status": "completed",
                    "summary": "done",
                }
            ]
        },
    )
    assert isinstance(event, BackgroundTasksCompleted)
    notice = event.tasks[0]
    assert notice.kind == "subagent"
    assert notice.exit_code is None
    assert notice.log_path is None


if __name__ == "__main__":
    test_sqlite_round_trip_and_wal()
    test_sqlite_conflicts_rollback()
    test_sqlite_schema_v1_migration()
    test_sqlite_schema_v3_migration()
    test_sqlite_command_task_round_trip()
    test_old_background_event_uses_defaults()
    test_sqlite_recovers_interrupted_approvals()
    test_sqlite_deletes_session_and_events()
    print("all ling_agent storage tests passed")

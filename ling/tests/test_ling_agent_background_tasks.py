"""Tests for background task scheduling and notification helpers."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from ling_agent import HarnessConfig
from ling_agent.background_tasks import (
    BackgroundTaskManager,
    background_notification_message,
    new_background_task_id,
)
from ling_agent.command_process import start_command_process
from ling_agent.events import BackgroundTaskNotice
from ling_agent.storage import SQLiteSessionStore, SessionRecord
from ling_agent.subagents import SubagentError, SubagentResult
from ling_agent.tools.subagents import create_subagent_tools
from ling_ai.types import ToolCall
from ling_core import ToolContext


class FakeRunner:
    def __init__(self, *, delay: float = 0.05) -> None:
        self.delay = delay
        self.cancelled = False

    async def spawn_explore(
        self,
        description: str,
        prompt: str,
        *,
        call_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> SubagentResult:
        await asyncio.sleep(self.delay)
        if cancel_event is not None and cancel_event.is_set():
            raise SubagentError("interrupted")
        return SubagentResult(
            summary=f"summary:{description}",
            trace=(),
        )


async def _manager(
    root: Path,
    *,
    concurrency: int = 2,
) -> tuple[BackgroundTaskManager, SQLiteSessionStore]:
    workspace = root / "workspace"
    workspace.mkdir()
    store = SQLiteSessionStore(root / "sessions.db")
    await store.upsert_session(
        SessionRecord(
            session_id="session-1",
            workspace_root=workspace,
            title="test",
            status="idle",
            created_at=1,
            updated_at=1,
        )
    )
    manager = BackgroundTaskManager(
        store,
        lambda workspace: FakeRunner(),
        HarnessConfig(background_task_concurrency=concurrency),
    )
    await manager.start()
    return manager, store


async def _submit(
    manager: BackgroundTaskManager,
    description: str,
    *,
    call_id: str,
) -> str:
    task = await manager.submit(
        session_id="session-1",
        workspace_root=Path("."),
        run_id="run-1",
        tool_call_id=call_id,
        description=description,
        prompt=f"prompt:{description}",
    )
    return task.id


def test_background_notification_message_lists_tasks() -> None:
    message = background_notification_message(
        (
            BackgroundTaskNotice(
                id="task-1",
                description="inspect auth",
                status="completed",
                summary="auth uses JWT",
            ),
            BackgroundTaskNotice(
                id="task-2",
                description="inspect db",
                status="failed",
                error="no access",
            ),
        )
    )
    text = message.content
    assert isinstance(text, str)
    assert "task-1" in text
    assert "auth uses JWT" in text
    assert "task-2" in text
    assert "no access" in text
    assert "Do not wait for another user message" in text


async def _run_manager_round_trip(root: Path) -> None:
    manager, store = await _manager(root)
    try:
        first = await _submit(manager, "first", call_id="call-1")
        second = await _submit(manager, "second", call_id="call-2")
        for _ in range(100):
            tasks = await manager.list("session-1")
            if all(
                task.status in ("completed", "failed")
                for task in tasks
            ):
                break
            await asyncio.sleep(0.01)
        tasks = await manager.list("session-1")
        assert {task.id for task in tasks} == {first, second}
        assert all(task.status == "completed" for task in tasks)
        assert all(task.summary for task in tasks)

        claimed = await manager.claim_notifications("session-1")
        assert {task.id for task in claimed} == {first, second}
        assert await manager.claim_notifications("session-1") == []
    finally:
        await manager.close()
        await store.close()


def test_manager_runs_and_batches_notifications() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_run_manager_round_trip(Path(temp)))


async def _stop_and_recover(root: Path) -> None:
    manager, store = await _manager(root)
    try:
        task = await manager.submit(
            session_id="session-1",
            workspace_root=root / "workspace",
            run_id="run-1",
            tool_call_id="call-stop",
            description="slow",
            prompt="slow",
        )
        await manager.stop(task.id)
        stopped = await manager.get(task.id)
        assert stopped is not None
        assert stopped.status == "cancelled"

        pending = await manager.submit(
            session_id="session-1",
            workspace_root=root / "workspace",
            run_id="run-1",
            tool_call_id="call-recover",
            description="recover",
            prompt="recover",
        )
        await manager.close()
        recovered = await store.get_background_task(pending.id)
        assert recovered is not None
        assert recovered.status == "interrupted"
    finally:
        await store.close()


def test_manager_stop_and_restart_recovery() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_stop_and_recover(Path(temp)))


async def _background_tool(root: Path) -> None:
    submitted: list[tuple[str, str, ToolCall]] = []
    runner = FakeRunner()

    async def submit(description: str, prompt: str, call: ToolCall):
        submitted.append((description, prompt, call))
        from ling_agent.storage import BackgroundTaskRecord

        return BackgroundTaskRecord(
            id="task-tool",
            session_id="session-1",
            run_id="run-1",
            tool_call_id=call.id,
            description=description,
            prompt=prompt,
            status="pending",
            summary=None,
            error=None,
            trace=(),
            created_at=1,
        )

    tools = create_subagent_tools(
        runner,
        submit_background=submit,
    )
    result = await tools[0].handler(
        ToolCall(
            id="call-background",
            name="spawn_subagent",
            arguments={
                "description": "inspect",
                "prompt": "inspect things",
                "run_in_background": True,
            },
        ),
        ToolContext(step=1),
    )
    assert submitted
    assert result.details["mode"] == "background"
    assert result.details["taskId"] == "task-tool"
    assert result.details["status"] == "pending"


def test_background_tool_returns_task_id() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_background_tool(Path(temp)))


def _sleep(seconds: float) -> str:
    if os.name == "nt":
        return f"Start-Sleep -Milliseconds {int(seconds * 1000)}"
    return f"sleep {seconds}"


def _echo(text: str) -> str:
    if os.name == "nt":
        return f"Write-Output {text}"
    return f"printf {text}"


async def _wait_status(
    store: SQLiteSessionStore,
    task_id: str,
    status: str,
) -> None:
    for _ in range(200):
        task = await store.get_background_task(task_id)
        if task is not None and task.status == status:
            return
        await asyncio.sleep(0.02)
    task = await store.get_background_task(task_id)
    raise AssertionError(f"task status: {task.status if task else None}")


async def _adopt_command(root: Path) -> None:
    manager, store = await _manager(root)
    handle = None
    try:
        workspace = root / "workspace"
        task_id = new_background_task_id()
        log_path = workspace / ".agents" / "background" / f"{task_id}.log"
        handle = await start_command_process(
            f"{_sleep(0.1)}; {_echo('cmd-ok')}",
            cwd=str(workspace),
            max_output_chars=2_000,
            task_id=task_id,
            log_path=str(log_path),
        )
        task = await manager.adopt_command(
            task_id=task_id,
            session_id="session-1",
            run_id="run-1",
            tool_call_id="call-command",
            command=f"{_sleep(0.1)}; {_echo('cmd-ok')}",
            handle=handle,
        )
        assert task.status == "running"
        assert task.kind == "command"
        assert task.output_path == str(log_path)
        assert manager._queue.qsize() == 0
        await _wait_status(store, task_id, "completed")
        finished = await store.get_background_task(task_id)
        assert finished is not None
        assert finished.exit_code == 0
        assert "cmd-ok" in (finished.summary or "")
        assert "cmd-ok" in log_path.read_text(encoding="utf-8")
    finally:
        if handle is not None:
            handle.close_log()
        await manager.close()
        await store.close()


def test_adopt_command_records_exit_and_log() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_adopt_command(Path(temp)))


async def _stop_command(root: Path) -> None:
    manager, store = await _manager(root)
    handle = None
    try:
        workspace = root / "workspace"
        task_id = new_background_task_id()
        log_path = workspace / ".agents" / "background" / f"{task_id}.log"
        handle = await start_command_process(
            _sleep(30),
            cwd=str(workspace),
            max_output_chars=1_000,
            task_id=task_id,
            log_path=str(log_path),
        )
        await manager.adopt_command(
            task_id=task_id,
            session_id="session-1",
            run_id="run-1",
            tool_call_id="call-stop",
            command=_sleep(30),
            handle=handle,
        )
        stopped = await manager.stop(task_id)
        assert stopped is not None and stopped.status == "cancelled"
        for _ in range(200):
            if handle.process.returncode is not None:
                break
            await asyncio.sleep(0.02)
        assert handle.process.returncode is not None
        late = await store.finish_background_task(
            task_id,
            status="completed",
            completed_at=999,
            exit_code=0,
        )
        assert late is not None and late.status == "cancelled"
    finally:
        if handle is not None:
            handle.close_log()
        await manager.close()
        await store.close()


def test_stop_command_kills_process_and_keeps_cancelled() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_stop_command(Path(temp)))


async def _close_command(root: Path) -> None:
    manager, store = await _manager(root)
    handle = None
    try:
        workspace = root / "workspace"
        task_id = new_background_task_id()
        log_path = workspace / ".agents" / "background" / f"{task_id}.log"
        handle = await start_command_process(
            _sleep(30),
            cwd=str(workspace),
            max_output_chars=1_000,
            task_id=task_id,
            log_path=str(log_path),
        )
        await manager.adopt_command(
            task_id=task_id,
            session_id="session-1",
            run_id="run-1",
            tool_call_id="call-close",
            command=_sleep(30),
            handle=handle,
        )
        await manager.close()
        row = await store.get_background_task(task_id)
        assert row is not None and row.status == "interrupted"
        assert handle.process.returncode is not None
    finally:
        if handle is not None:
            handle.close_log()
        await store.close()


def test_close_terminates_command_and_marks_interrupted() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_close_command(Path(temp)))


def test_command_notification_includes_exit_code_and_log() -> None:
    message = background_notification_message(
        (
            BackgroundTaskNotice(
                id="task-command",
                description="npm run build",
                status="completed",
                summary="built ok",
                kind="command",
                exit_code=0,
                log_path="/tmp/task-command.log",
            ),
        )
    )
    text = message.content
    assert "run_command: npm run build" in text
    assert "exit code 0" in text
    assert "Full log: /tmp/task-command.log" in text
    assert "built ok" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

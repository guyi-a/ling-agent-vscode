"""Background explore-subagent tasks and their in-process scheduler."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from ling_ai.types import UserMessage

from .command_process import CommandHandle
from .config import HarnessConfig
from .events import BackgroundTaskNotice
from .storage import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
    SQLiteSessionStore,
)
from .subagents import SubagentError, SubagentRunner
from .workspace import Workspace

TaskUpdateSink = Callable[[BackgroundTaskRecord], None | Awaitable[None]]
TaskTerminalSink = Callable[[str], None | Awaitable[None]]
RunnerFactory = Callable[[Workspace], SubagentRunner]

MAX_TRACE_BYTES = 64 * 1024


def _now_ms() -> int:
    return int(time.time() * 1000)


def new_background_task_id() -> str:
    return f"task-{uuid.uuid4()}"


def command_description(command: str, limit: int = 120) -> str:
    line = next(
        (line.strip() for line in command.splitlines() if line.strip()),
        command.strip(),
    )
    return line[:limit]


@dataclass(slots=True)
class _RunningTask:
    cancel_event: asyncio.Event
    handle: CommandHandle | None = None
    monitor: asyncio.Task[None] | None = None
    terminate_task: asyncio.Task[None] | None = None


class BackgroundTaskManager:
    """Persist, queue, execute, and stop background explore subagents."""

    def __init__(
        self,
        store: SQLiteSessionStore,
        runner_factory: RunnerFactory,
        config: HarnessConfig,
        *,
        on_updated: TaskUpdateSink | None = None,
        on_terminal: TaskTerminalSink | None = None,
    ) -> None:
        self._store = store
        self._runner_factory = runner_factory
        self._config = config
        self._on_updated = on_updated
        self._on_terminal = on_terminal
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._running: dict[str, _RunningTask] = {}
        self._started = False
        self._closed = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._started or self._closed:
                return
            self._started = True
            interrupted = await self._store.recover_background_tasks(
                completed_at=_now_ms()
            )
            for task in interrupted:
                await self._emit_updated(task)
            self._workers = [
                asyncio.create_task(
                    self._worker(),
                    name=f"ling-background-task-{index}",
                )
                for index in range(self._config.background_task_concurrency)
            ]

    async def submit(
        self,
        *,
        session_id: str,
        workspace_root: str | Path,
        run_id: str,
        tool_call_id: str,
        description: str,
        prompt: str,
    ) -> BackgroundTaskRecord:
        if self._closed:
            raise RuntimeError("Background task manager is closed")
        if not self._started:
            await self.start()
        name = description.strip()
        body = prompt.strip()
        if not name or not body:
            raise ValueError("Background task description and prompt are required")
        task = BackgroundTaskRecord(
            id=new_background_task_id(),
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            description=name,
            prompt=body,
            status="pending",
            summary=None,
            error=None,
            trace=(),
            created_at=_now_ms(),
        )
        await self._store.create_background_task(task)
        await self._emit_updated(task)
        await self._queue.put(task.id)
        return task

    async def adopt_command(
        self,
        *,
        task_id: str,
        session_id: str,
        run_id: str,
        tool_call_id: str,
        command: str,
        handle: CommandHandle,
    ) -> BackgroundTaskRecord:
        if self._closed:
            raise RuntimeError("Background task manager is closed")
        if not self._started:
            await self.start()
        body = command.strip()
        if not body:
            raise ValueError("Background command must not be empty")
        task = BackgroundTaskRecord(
            id=task_id,
            session_id=session_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            description=command_description(body),
            prompt=body,
            status="running",
            summary=None,
            error=None,
            trace=(),
            created_at=_now_ms(),
            started_at=handle.started_at,
            kind="command",
            exit_code=None,
            output_path=handle.log_path,
            pid=handle.pid,
        )
        running = _RunningTask(
            cancel_event=asyncio.Event(),
            handle=handle,
        )
        self._running[task_id] = running
        try:
            await self._store.create_background_task(task)
            if self._closed:
                await self._store.finish_background_task(
                    task_id,
                    status="interrupted",
                    completed_at=_now_ms(),
                    error="The background task manager was closed.",
                )
                raise RuntimeError("Background task manager is closed")
            await self._emit_updated(task)
            running.monitor = asyncio.create_task(
                self._monitor_command(task_id, running),
                name=f"ling-background-command-{task_id}",
            )
        except BaseException:
            self._running.pop(task_id, None)
            with suppress(Exception):
                await self._store.finish_background_task(
                    task_id,
                    status="interrupted",
                    completed_at=_now_ms(),
                    error="The command could not be adopted.",
                )
            raise
        return task

    async def list(
        self,
        session_id: str,
    ) -> list[BackgroundTaskRecord]:
        return await self._store.list_background_tasks(session_id)

    async def get(self, task_id: str) -> BackgroundTaskRecord | None:
        return await self._store.get_background_task(task_id)

    async def stop(self, task_id: str) -> BackgroundTaskRecord | None:
        task = await self._store.get_background_task(task_id)
        if task is None:
            raise ValueError(f"Unknown background task: {task_id}")
        if task.status not in ("pending", "running"):
            return task
        running = self._running.get(task_id)
        if running is not None:
            running.cancel_event.set()
        updated = await self._store.finish_background_task(
            task_id,
            status="cancelled",
            completed_at=_now_ms(),
            error="Cancelled by the user.",
        )
        if updated is not None:
            await self._emit_updated(updated)
        if running is not None and running.handle is not None:
            handle = running.handle
            if (
                running.terminate_task is None
                or running.terminate_task.done()
            ):
                running.terminate_task = asyncio.create_task(
                    self._terminate_command(task_id, handle),
                    name=f"ling-background-stop-{task_id}",
                )
        return updated

    async def _terminate_command(
        self,
        task_id: str,
        handle: CommandHandle,
    ) -> None:
        try:
            await handle.terminate()
            await handle.drain_readers(timeout=5.0)
        finally:
            handle.close_log()
            self._running.pop(task_id, None)

    async def claim_notifications(
        self,
        session_id: str,
    ) -> list[BackgroundTaskRecord]:
        return await self._store.claim_background_tasks(
            session_id,
            claimed_at=_now_ms(),
        )

    async def release_notifications(
        self,
        task_ids: list[str],
    ) -> None:
        await self._store.release_background_tasks(task_ids)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        command_handles: list[CommandHandle] = []
        for task_id, running in list(self._running.items()):
            running.cancel_event.set()
            if running.handle is None:
                continue
            await self._store.finish_background_task(
                task_id,
                status="interrupted",
                completed_at=_now_ms(),
                error="The sidecar stopped before this command finished.",
            )
            command_handles.append(running.handle)

        if command_handles:
            terminators = [
                asyncio.create_task(handle.terminate())
                for handle in command_handles
            ]
            done, pending = await asyncio.wait(terminators, timeout=10.0)
            for task in pending:
                task.cancel()
            for task in done:
                with suppress(Exception):
                    task.result()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for handle in command_handles:
                handle.close_log()

        running_tasks = [
            task
            for running in self._running.values()
            for task in (running.monitor, running.terminate_task)
            if task is not None and not task.done()
        ]
        for task in running_tasks:
            task.cancel()
        if running_tasks:
            await asyncio.gather(*running_tasks, return_exceptions=True)
        self._running.clear()

        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        interrupted = await self._store.recover_background_tasks(
            completed_at=_now_ms()
        )
        for task in interrupted:
            await self._emit_updated(task)

    async def _worker(self) -> None:
        while not self._closed:
            try:
                task_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._run_task(task_id)
            finally:
                self._queue.task_done()

    async def _monitor_command(
        self,
        task_id: str,
        running: _RunningTask,
    ) -> None:
        handle = running.handle
        if handle is None:
            return
        try:
            exit_code = await handle.wait_background(
                drain_timeout=(
                    self._config.background_command_drain_timeout_seconds
                )
            )
        except asyncio.CancelledError:
            handle.close_log()
            self._running.pop(task_id, None)
            raise
        except Exception as exc:
            handle.close_log()
            self._running.pop(task_id, None)
            updated = await self._store.finish_background_task(
                task_id,
                status="failed",
                completed_at=_now_ms(),
                error=str(exc) or type(exc).__name__,
            )
            if updated is not None:
                await self._emit_updated(updated)
                await self._emit_terminal(updated.session_id)
            return

        handle.close_log()
        self._running.pop(task_id, None)
        current = await self._store.get_background_task(task_id)
        if current is None or current.status not in ("pending", "running"):
            return
        status: BackgroundTaskStatus = (
            "completed" if exit_code == 0 else "failed"
        )
        summary = handle.tail(
            self._config.background_command_tail_chars
        )
        updated = await self._store.finish_background_task(
            task_id,
            status=status,
            completed_at=_now_ms(),
            summary=summary or None,
            exit_code=exit_code,
        )
        if updated is not None:
            await self._emit_updated(updated)
            await self._emit_terminal(updated.session_id)

    async def _run_task(self, task_id: str) -> None:
        task = await self._store.get_background_task(task_id)
        if task is None or task.status != "pending":
            return
        running = _RunningTask(cancel_event=asyncio.Event())
        self._running[task_id] = running
        task = await self._store.mark_background_task_running(
            task_id,
            started_at=_now_ms(),
        )
        if task is None:
            self._running.pop(task_id, None)
            return
        await self._emit_updated(task)

        try:
            session = await self._store.get_session(task.session_id)
            if session is None:
                raise RuntimeError("The parent session no longer exists")
            workspace = Workspace(
                session.workspace_root,
                self._config,
            )
            runner = self._runner_factory(workspace)
            result = await runner.spawn_explore(
                prompt=task.prompt,
                description=task.description,
                call_id=task.tool_call_id or task.id,
                cancel_event=running.cancel_event,
            )
            current = await self._store.get_background_task(task_id)
            if current is not None and current.status == "cancelled":
                return
            updated = await self._store.finish_background_task(
                task_id,
                status="completed",
                completed_at=_now_ms(),
                summary=result.summary,
                trace=_trim_trace(result.trace),
            )
            if updated is not None:
                await self._emit_updated(updated)
                await self._emit_terminal(updated.session_id)
        except asyncio.CancelledError:
            await self._store.finish_background_task(
                task_id,
                status="interrupted",
                completed_at=_now_ms(),
                error="The sidecar stopped before this task completed.",
            )
            raise
        except SubagentError as exc:
            current = await self._store.get_background_task(task_id)
            if current is not None and current.status == "cancelled":
                return
            updated = await self._store.finish_background_task(
                task_id,
                status="failed",
                completed_at=_now_ms(),
                error=str(exc),
            )
            if updated is not None:
                await self._emit_updated(updated)
                await self._emit_terminal(updated.session_id)
        except Exception as exc:
            current = await self._store.get_background_task(task_id)
            if current is not None and current.status == "cancelled":
                return
            updated = await self._store.finish_background_task(
                task_id,
                status="failed",
                completed_at=_now_ms(),
                error=str(exc) or type(exc).__name__,
            )
            if updated is not None:
                await self._emit_updated(updated)
                await self._emit_terminal(updated.session_id)
        finally:
            self._running.pop(task_id, None)

    async def _emit_updated(self, task: BackgroundTaskRecord) -> None:
        if self._on_updated is None:
            return
        result = self._on_updated(task)
        if inspect.isawaitable(result):
            await result

    async def _emit_terminal(self, session_id: str) -> None:
        if self._on_terminal is None:
            return
        result = self._on_terminal(session_id)
        if inspect.isawaitable(result):
            await result


def background_notification_message(
    tasks: tuple[BackgroundTaskNotice, ...],
) -> UserMessage:
    lines = [
        "<background-tasks-finished>",
        "The following background tasks have finished:",
    ]
    for task in tasks:
        if task.kind == "command":
            exit_suffix = (
                f" (exit code {task.exit_code})"
                if task.exit_code is not None
                else ""
            )
            lines.append(
                f"- [{task.id}] run_command: {task.description} — "
                f"{task.status}{exit_suffix}"
            )
            if task.log_path:
                lines.append(f"  Full log: {task.log_path}")
            if task.summary:
                lines.append("  Output tail:")
                lines.extend(
                    f"  {line}" for line in task.summary.splitlines()
                )
            elif task.error:
                lines.append(f"  Error: {task.error}")
        else:
            lines.append(
                f"- [{task.id}] {task.description} — {task.status}"
            )
            if task.summary:
                lines.append(f"  Summary: {task.summary}")
            elif task.error:
                lines.append(f"  Error: {task.error}")
    lines.extend(
        [
            "Continue the original user request now using these results. "
            "Do not wait for another user message merely to acknowledge "
            "this notification.",
            "</background-tasks-finished>",
        ]
    )
    return UserMessage(
        content="\n".join(lines),
        timestamp=_now_ms(),
    )


def _trim_trace(
    trace: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    if not trace:
        return ()
    encoded = json.dumps(
        list(trace),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) <= MAX_TRACE_BYTES:
        return trace
    kept: list[dict[str, object]] = []
    size = 0
    for entry in trace:
        item_size = len(
            json.dumps(entry, ensure_ascii=False).encode("utf-8")
        )
        if kept and size + item_size > MAX_TRACE_BYTES:
            break
        kept.append(entry)
        size += item_size
    return tuple(kept)


__all__ = [
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "BackgroundTaskStatus",
    "background_notification_message",
    "command_description",
    "new_background_task_id",
]

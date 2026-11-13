"""Tests for the foreground-command detach state machine."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from ling_agent.background_tasks import BackgroundTaskManager
from ling_agent.command_calls import (
    CommandCallRegistry,
    CommandCallState,
    CommandNotRunning,
)
from ling_agent.command_process import start_command_process
from ling_agent.config import HarnessConfig
from ling_agent.storage import SQLiteSessionStore, SessionRecord


def _sleep(seconds: float) -> str:
    if os.name == "nt":
        return f"Start-Sleep -Milliseconds {int(seconds * 1000)}"
    return f"sleep {seconds}"


def _echo(text: str) -> str:
    if os.name == "nt":
        return f"Write-Output {text}"
    return f"printf {text}"


async def _setup(root: Path):
    workspace = root / "ws"
    workspace.mkdir()
    store = SQLiteSessionStore(root / "sessions.db")
    await store.upsert_session(
        SessionRecord(
            session_id="s1",
            workspace_root=workspace,
            title="t",
            status="idle",
            created_at=1,
            updated_at=1,
        )
    )
    manager = BackgroundTaskManager(
        store,
        lambda _workspace: None,
        HarnessConfig(),
    )
    await manager.start()
    registry = CommandCallRegistry(manager, workspace)
    return manager, store, registry, workspace


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


async def _start(
    registry: CommandCallRegistry,
    workspace: Path,
    command: str,
):
    task_id, log_path = registry.allocate()
    handle = await start_command_process(
        command,
        cwd=str(workspace),
        max_output_chars=2_000,
        task_id=task_id,
        log_path=log_path,
    )
    slot = registry.register(
        "call-1",
        command,
        task_id,
        log_path,
        handle,
    )
    return task_id, log_path, handle, slot


async def _double_detach(root: Path) -> None:
    manager, store, registry, workspace = await _setup(root)
    try:
        registry.begin_run("s1", "r1")
        task_id, _log, handle, slot = await _start(
            registry,
            workspace,
            f"{_sleep(0.4)}; {_echo('once')}",
        )
        waiter = asyncio.create_task(
            handle.wait_foreground(
                timeout_seconds=10,
                detach_event=slot.detach_event,
            )
        )
        await asyncio.sleep(0.1)
        first, second = await asyncio.gather(
            registry.request_detach("call-1"),
            registry.request_detach("call-1"),
        )
        assert first.id == task_id
        assert second.id == task_id
        outcome = await waiter
        assert outcome.reason == "detached"
        assert (
            registry.complete_foreground("call-1")
            is CommandCallState.DETACHED
        )
        await _wait_status(store, task_id, "completed")
    finally:
        await registry.end_run()
        await manager.close()
        await store.close()


def test_double_detach_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_double_detach(Path(temp)))


async def _detach_survives_cancel(root: Path) -> None:
    manager, store, registry, workspace = await _setup(root)
    try:
        registry.begin_run("s1", "r1")
        task_id, _log, handle, slot = await _start(
            registry,
            workspace,
            f"{_sleep(0.5)}; {_echo('alive')}",
        )
        cancel = asyncio.Event()
        waiter = asyncio.create_task(
            handle.wait_foreground(
                timeout_seconds=10,
                cancel_event=cancel,
                detach_event=slot.detach_event,
            )
        )
        await asyncio.sleep(0.1)
        await registry.request_detach("call-1")
        cancel.set()
        outcome = await waiter
        assert outcome.reason == "detached"
        assert handle.process.returncode is None
        await _wait_status(store, task_id, "completed")
    finally:
        await registry.end_run()
        await manager.close()
        await store.close()


def test_detach_survives_parent_cancel() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_detach_survives_cancel(Path(temp)))


async def _cancel_before_detach(root: Path) -> None:
    manager, store, registry, workspace = await _setup(root)
    try:
        registry.begin_run("s1", "r1")
        _task_id, _log, handle, slot = await _start(
            registry,
            workspace,
            _sleep(30),
        )
        cancel = asyncio.Event()
        waiter = asyncio.create_task(
            handle.wait_foreground(
                timeout_seconds=60,
                cancel_event=cancel,
                detach_event=slot.detach_event,
            )
        )
        await asyncio.sleep(0.1)
        cancel.set()
        outcome = await waiter
        assert outcome.reason == "cancelled"
        assert handle.process.returncode is not None
        assert (
            registry.complete_foreground("call-1")
            is CommandCallState.FINISHED
        )
    finally:
        await registry.end_run()
        await manager.close()
        await store.close()


def test_cancel_before_detach_kills_process() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_cancel_before_detach(Path(temp)))


class _FailingManager:
    async def adopt_command(self, **_kwargs: object) -> None:
        raise RuntimeError("adopt failed")


async def _adoption_failure_terminates(root: Path) -> None:
    workspace = root / "ws"
    workspace.mkdir()
    registry = CommandCallRegistry(_FailingManager(), workspace)  # type: ignore[arg-type]
    registry.begin_run("s1", "r1")
    _task_id, _log, handle, slot = await _start(
        registry,
        workspace,
        _sleep(30),
    )
    waiter = asyncio.create_task(
        handle.wait_foreground(
            timeout_seconds=60,
            detach_event=slot.detach_event,
        )
    )
    await asyncio.sleep(0.1)
    with pytest.raises(CommandNotRunning):
        await registry.request_detach("call-1")
    outcome = await waiter
    assert outcome.reason == "detached"
    assert handle.process.returncode is not None
    await registry.end_run()


def test_adoption_failure_terminates_process() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_adoption_failure_terminates(Path(temp)))


async def _end_run_terminates_unadopted(root: Path) -> None:
    manager, store, registry, workspace = await _setup(root)
    try:
        registry.begin_run("s1", "r1")
        _task_id, _log, handle, slot = await _start(
            registry,
            workspace,
            _sleep(30),
        )
        waiter = asyncio.create_task(
            handle.wait_foreground(
                timeout_seconds=60,
                detach_event=slot.detach_event,
            )
        )
        await asyncio.sleep(0.1)
        await registry.end_run()
        await waiter
        assert handle.process.returncode is not None
    finally:
        await manager.close()
        await store.close()


def test_end_run_terminates_unadopted_command() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_end_run_terminates_unadopted(Path(temp)))


async def _end_run_keeps_detached(root: Path) -> None:
    manager, store, registry, workspace = await _setup(root)
    try:
        registry.begin_run("s1", "r1")
        task_id, _log, handle, slot = await _start(
            registry,
            workspace,
            f"{_sleep(0.4)}; {_echo('kept')}",
        )
        waiter = asyncio.create_task(
            handle.wait_foreground(
                timeout_seconds=10,
                detach_event=slot.detach_event,
            )
        )
        await asyncio.sleep(0.1)
        await registry.request_detach("call-1")
        await waiter
        registry.complete_foreground("call-1")
        await registry.end_run()
        assert handle.process.returncode is None
        await _wait_status(store, task_id, "completed")
    finally:
        await manager.close()
        await store.close()


def test_end_run_keeps_detached_command() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_end_run_keeps_detached(Path(temp)))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

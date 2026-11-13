"""Active foreground command calls and their detach state machine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .background_tasks import (
    BackgroundTaskManager,
    new_background_task_id,
)
from .command_process import CommandCallState, CommandHandle
from .storage import BackgroundTaskRecord


class CommandNotRunning(RuntimeError):
    """The command is no longer waiting in the foreground."""


@dataclass(slots=True)
class DetachOutcome:
    task: BackgroundTaskRecord | None = None
    error: str | None = None


@dataclass(slots=True)
class CommandCallSlot:
    call_id: str
    command: str
    task_id: str
    log_path: str
    handle: CommandHandle
    session_id: str
    run_id: str
    state: CommandCallState
    detach_event: asyncio.Event
    outcome: asyncio.Future[DetachOutcome]
    adopt_task: asyncio.Task[None] | None = None

    def resolve(self, outcome: DetachOutcome) -> None:
        if not self.outcome.done():
            self.outcome.set_result(outcome)


class CommandCallRegistry:
    """Track running foreground commands and atomically hand them off."""

    def __init__(
        self,
        manager: BackgroundTaskManager | None,
        workspace_root: Path,
    ) -> None:
        self._manager = manager
        self._workspace_root = workspace_root
        self._slots: dict[str, CommandCallSlot] = {}
        self._session_id: str | None = None
        self._run_id: str | None = None

    @property
    def available(self) -> bool:
        return self._manager is not None

    def begin_run(self, session_id: str, run_id: str) -> None:
        self._session_id = session_id
        self._run_id = run_id
        self._slots.clear()

    def allocate(self) -> tuple[str, str]:
        if self._session_id is None or self._run_id is None:
            raise CommandNotRunning("No active run")
        task_id = new_background_task_id()
        log_path = (
            self._workspace_root
            / ".agents"
            / "background"
            / f"{task_id}.log"
        )
        return task_id, str(log_path)

    def register(
        self,
        call_id: str,
        command: str,
        task_id: str,
        log_path: str,
        handle: CommandHandle,
    ) -> CommandCallSlot:
        if self._session_id is None or self._run_id is None:
            raise CommandNotRunning("No active run")
        slot = CommandCallSlot(
            call_id=call_id,
            command=command,
            task_id=task_id,
            log_path=log_path,
            handle=handle,
            session_id=self._session_id,
            run_id=self._run_id,
            state=CommandCallState.RUNNING,
            detach_event=asyncio.Event(),
            outcome=asyncio.get_running_loop().create_future(),
        )
        self._slots[call_id] = slot
        return slot

    def state(self, call_id: str) -> CommandCallState | None:
        slot = self._slots.get(call_id)
        return slot.state if slot is not None else None

    def complete_foreground(self, call_id: str) -> CommandCallState:
        slot = self._slots.get(call_id)
        if slot is None:
            return CommandCallState.FINISHED
        if slot.state is CommandCallState.RUNNING:
            slot.state = CommandCallState.FINISHED
        return slot.state

    def mark_terminating(self, call_id: str) -> bool:
        slot = self._slots.get(call_id)
        if slot is None:
            return True
        if slot.state in (
            CommandCallState.DETACHING,
            CommandCallState.DETACHED,
        ):
            return False
        if slot.state is CommandCallState.RUNNING:
            slot.state = CommandCallState.TERMINATING
            slot.resolve(DetachOutcome(error="The command was stopped."))
        return True

    async def request_detach(self, call_id: str) -> BackgroundTaskRecord:
        slot = self._slots.get(call_id)
        if slot is None and self._run_id is not None:
            # The tool card turns Running when ToolExecutionStarted is
            # committed, just before the handler registers its slot.
            for _ in range(50):
                await asyncio.sleep(0.01)
                slot = self._slots.get(call_id)
                if slot is not None:
                    break
        if slot is None:
            raise CommandNotRunning("The command is no longer running")
        if slot.state in (
            CommandCallState.FINISHED,
            CommandCallState.TERMINATING,
        ):
            raise CommandNotRunning("The command is no longer running")
        if slot.state is CommandCallState.RUNNING:
            if slot.handle.finished:
                slot.state = CommandCallState.FINISHED
                raise CommandNotRunning("The command already finished")
            slot.state = CommandCallState.DETACHING
            slot.detach_event.set()
            slot.adopt_task = asyncio.create_task(
                self._adopt(slot),
                name=f"ling-adopt-command-{slot.task_id}",
            )
        outcome = await asyncio.shield(slot.outcome)
        if outcome.error is not None or outcome.task is None:
            raise CommandNotRunning(
                outcome.error or "The command could not be backgrounded"
            )
        return outcome.task

    async def background_outcome(
        self,
        call_id: str,
    ) -> DetachOutcome | None:
        slot = self._slots.get(call_id)
        if slot is None:
            return None
        if slot.state in (
            CommandCallState.DETACHING,
            CommandCallState.DETACHED,
            CommandCallState.TERMINATING,
        ):
            return await asyncio.shield(slot.outcome)
        return None

    async def _adopt(self, slot: CommandCallSlot) -> None:
        manager = self._manager
        try:
            if manager is None:
                raise RuntimeError("Background tasks are not available")
            task = await manager.adopt_command(
                task_id=slot.task_id,
                session_id=slot.session_id,
                run_id=slot.run_id,
                tool_call_id=slot.call_id,
                command=slot.command,
                handle=slot.handle,
            )
        except asyncio.CancelledError:
            slot.state = CommandCallState.TERMINATING
            await self._terminate_handle(slot, delete_log=True)
            slot.resolve(
                DetachOutcome(error="Background adoption was cancelled.")
            )
            raise
        except Exception as exc:
            slot.state = CommandCallState.TERMINATING
            await self._terminate_handle(slot, delete_log=True)
            slot.resolve(
                DetachOutcome(
                    error=str(exc) or type(exc).__name__,
                )
            )
            return
        slot.state = CommandCallState.DETACHED
        slot.handle.flush_log()
        slot.resolve(DetachOutcome(task=task))

    async def _terminate_handle(
        self,
        slot: CommandCallSlot,
        *,
        delete_log: bool,
    ) -> None:
        await slot.handle.terminate()
        await slot.handle.drain_readers(timeout=5.0)
        slot.handle.close_log(delete=delete_log)

    async def end_run(self) -> None:
        slots = list(self._slots.values())
        for slot in slots:
            if slot.state is CommandCallState.RUNNING:
                slot.state = CommandCallState.TERMINATING
                await self._terminate_handle(slot, delete_log=True)
            elif slot.state is CommandCallState.FINISHED:
                slot.handle.close_log(delete=True)
            elif slot.state is CommandCallState.TERMINATING:
                await self._terminate_handle(slot, delete_log=True)
            elif slot.state is CommandCallState.DETACHING:
                adopt_task = slot.adopt_task
                if adopt_task is not None and not adopt_task.done():
                    await asyncio.wait({adopt_task}, timeout=5.0)
        self._slots.clear()
        self._session_id = None
        self._run_id = None

    async def close(self) -> None:
        await self.end_run()


__all__ = [
    "CommandCallRegistry",
    "CommandCallSlot",
    "CommandCallState",
    "CommandNotRunning",
    "DetachOutcome",
]

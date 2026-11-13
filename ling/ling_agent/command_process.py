"""Managed command processes with optional foreground-to-background handoff."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import IO, Literal

from ling_core import ToolExecution

ForegroundReason = Literal["exit", "timeout", "cancelled", "detached"]


class CommandCallState(str, Enum):
    RUNNING = "running"
    DETACHING = "detaching"
    DETACHED = "detached"
    TERMINATING = "terminating"
    FINISHED = "finished"


@dataclass(slots=True)
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    backgrounded: bool = False
    task_id: str | None = None
    log_path: str | None = None

    def to_tool_execution(self) -> ToolExecution:
        if self.backgrounded:
            sections = [
                "mode: background",
                f"task_id: {self.task_id or '(unknown)'}",
                "status: running",
            ]
            if self.log_path:
                sections.append(f"log_path: {self.log_path}")
            sections.extend(
                [
                    "",
                    "The command is still running in the background. Its exit "
                    "code and output tail will be delivered automatically when "
                    "it finishes. Continue with other work; do not poll or wait "
                    "for it.",
                ]
            )
            if self.stdout:
                sections.extend(["", "stdout (so far):", self.stdout])
            if self.stderr:
                sections.extend(["", "stderr (so far):", self.stderr])
            details = {
                "kind": "command",
                "mode": "background",
                "taskId": self.task_id,
                "logPath": self.log_path,
                "status": "running",
                "exitCode": None,
            }
            return ToolExecution(
                output="\n".join(sections),
                details=details,
            )

        sections = [
            f"exit_code: {self.exit_code}",
            f"timed_out: {str(self.timed_out).lower()}",
            f"cancelled: {str(self.cancelled).lower()}",
            "",
            "stdout:",
            self.stdout or "(empty)",
            "",
            "stderr:",
            self.stderr or "(empty)",
        ]
        if self.truncated:
            sections.extend(["", "[truncated]"])
        return ToolExecution(
            output="\n".join(sections),
            is_error=(
                self.cancelled
                or self.timed_out
                or (self.exit_code is not None and self.exit_code != 0)
            ),
            details={
                "kind": "command",
                "mode": "foreground",
                "exitCode": self.exit_code,
                "timedOut": self.timed_out,
                "cancelled": self.cancelled,
                "truncated": self.truncated,
            },
        )


@dataclass(slots=True)
class ForegroundOutcome:
    reason: ForegroundReason
    exit_code: int | None = None


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def take(self, text: str) -> tuple[str, bool]:
        if not text:
            return "", False
        if self.remaining <= 0:
            return "", True
        selected = text[: self.remaining]
        self.remaining -= len(selected)
        return selected, len(selected) < len(text)


class CommandOutput:
    """Collect model-facing output, a rolling tail, and an optional log file."""

    def __init__(
        self,
        *,
        max_output_chars: int,
        tail_chars: int,
        max_log_chars: int,
    ) -> None:
        self._budget = _OutputBudget(max_output_chars)
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._truncated = False
        self._tail_chars = max(1, tail_chars)
        self._tail = ""
        self._max_log_chars = max(1, max_log_chars)
        self._log_path: str | None = None
        self._log_file: IO[str] | None = None
        self._log_chars = 0
        self._log_truncated = False
        self._last_flush = 0.0

    @property
    def log_path(self) -> str | None:
        return self._log_path

    def open_log(self, path: str) -> bool:
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = target.open("w", encoding="utf-8")
        except OSError:
            self._log_file = None
            self._log_path = None
            return False
        self._log_path = path
        self._last_flush = time.monotonic()
        return True

    def append(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        if not text:
            return
        self._tail = (self._tail + text)[-self._tail_chars :]
        self._write_log(text)
        selected, dropped = self._budget.take(text)
        if selected:
            if stream == "stdout":
                self._stdout.append(selected)
            else:
                self._stderr.append(selected)
        self._truncated = self._truncated or dropped

    def _write_log(self, text: str) -> None:
        log = self._log_file
        if log is None:
            return
        remaining = self._max_log_chars - self._log_chars
        if remaining <= 0:
            self._truncate_log()
            return
        try:
            log.write(text[:remaining])
            self._log_chars += min(len(text), remaining)
            if len(text) > remaining:
                self._truncate_log()
                return
            now = time.monotonic()
            if now - self._last_flush >= 0.25:
                log.flush()
                self._last_flush = now
        except OSError:
            self.close_log()

    def _truncate_log(self) -> None:
        if self._log_truncated:
            return
        self._log_truncated = True
        log = self._log_file
        if log is not None:
            with suppress(OSError):
                log.write("\n[log truncated]\n")
                log.flush()
        self.close_log()

    def flush(self) -> None:
        if self._log_file is not None:
            with suppress(OSError):
                self._log_file.flush()
            self._last_flush = time.monotonic()

    def close_log(self, *, delete: bool = False) -> None:
        log = self._log_file
        self._log_file = None
        if log is not None:
            with suppress(OSError):
                log.flush()
                log.close()
        if delete and self._log_path is not None:
            with suppress(OSError):
                Path(self._log_path).unlink()
            self._log_path = None

    def captures(self) -> tuple[str, str, bool]:
        return (
            "".join(self._stdout),
            "".join(self._stderr),
            self._truncated,
        )

    def tail(self, limit: int | None = None) -> str:
        if limit is None or limit <= 0:
            return self._tail
        return self._tail[-limit:]


async def _read_stream(
    stream: asyncio.StreamReader,
    output: CommandOutput,
    name: Literal["stdout", "stderr"],
) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        output.append(name, decoder.decode(chunk))
    output.append(name, decoder.decode(b"", final=True))


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(killer.wait(), timeout=5)
        except OSError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            await asyncio.sleep(0.2)
        except ProcessLookupError:
            pass
        except OSError:
            pass

    if process.returncode is None:
        with suppress(ProcessLookupError):
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


class CommandHandle:
    """Own a running process and its output until it is detached or finishes."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        command: str,
        output: CommandOutput,
        stdout_task: asyncio.Task[None],
        stderr_task: asyncio.Task[None],
        started_at: int,
        task_id: str | None = None,
    ) -> None:
        self.process = process
        self.command = command
        self.output = output
        self.stdout_task = stdout_task
        self.stderr_task = stderr_task
        self.started_at = started_at
        self.task_id = task_id
        self._terminate_task: asyncio.Task[None] | None = None

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def log_path(self) -> str | None:
        return self.output.log_path

    @property
    def finished(self) -> bool:
        return self.process.returncode is not None

    async def wait_foreground(
        self,
        *,
        timeout_seconds: float,
        cancel_event: asyncio.Event | None = None,
        detach_event: asyncio.Event | None = None,
    ) -> ForegroundOutcome:
        wait_task = asyncio.create_task(self.process.wait())
        timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
        cancel_task = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        detach_task = (
            asyncio.create_task(detach_event.wait())
            if detach_event is not None
            else None
        )
        tasks = {wait_task, timeout_task}
        if cancel_task is not None:
            tasks.add(cancel_task)
        if detach_task is not None:
            tasks.add(detach_task)

        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if detach_task is not None and detach_task in done:
            outcome = ForegroundOutcome("detached")
        elif wait_task in done:
            outcome = ForegroundOutcome(
                "exit",
                exit_code=self.process.returncode,
            )
        elif cancel_task is not None and cancel_task in done:
            await self.terminate()
            await self.drain_readers(timeout=5.0)
            outcome = ForegroundOutcome("cancelled")
        else:
            await self.terminate()
            await self.drain_readers(timeout=5.0)
            outcome = ForegroundOutcome("timeout")

        for task in (wait_task, timeout_task, cancel_task, detach_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        return outcome

    async def wait_background(self, *, drain_timeout: float = 2.0) -> int | None:
        exit_code = await self.process.wait()
        await self.drain_readers(timeout=drain_timeout)
        return exit_code

    async def drain_readers(self, *, timeout: float | None = None) -> None:
        tasks = {self.stdout_task, self.stderr_task}
        if timeout is None:
            await asyncio.gather(*tasks, return_exceptions=True)
            return
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            with suppress(Exception):
                task.result()

    async def terminate(self) -> None:
        if self._terminate_task is None:
            self._terminate_task = asyncio.create_task(
                _terminate_process_tree(self.process)
            )
        await asyncio.shield(self._terminate_task)

    def flush_log(self) -> None:
        self.output.flush()

    def close_log(self, *, delete: bool = False) -> None:
        self.output.close_log(delete=delete)

    def tail(self, limit: int | None = None) -> str:
        return self.output.tail(limit)

    def foreground_result(self, outcome: ForegroundOutcome) -> CommandResult:
        stdout, stderr, truncated = self.output.captures()
        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=outcome.exit_code,
            timed_out=outcome.reason == "timeout",
            cancelled=outcome.reason == "cancelled",
            truncated=truncated,
        )

    def background_result(self, task_id: str) -> CommandResult:
        stdout, stderr, truncated = self.output.captures()
        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=None,
            truncated=truncated,
            backgrounded=True,
            task_id=task_id,
            log_path=self.output.log_path,
        )


async def start_command_process(
    command: str,
    *,
    cwd: str,
    max_output_chars: int,
    tail_chars: int = 8_000,
    max_log_chars: int = 20_000_000,
    task_id: str | None = None,
    log_path: str | None = None,
) -> CommandHandle:
    if not command.strip():
        raise ValueError("command must not be empty")

    if os.name == "nt":
        powershell = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
            + command
        )
        args = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            powershell,
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=os.environ.copy(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=cwd,
            env=os.environ.copy(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    assert process.stdout is not None
    assert process.stderr is not None
    output = CommandOutput(
        max_output_chars=max_output_chars,
        tail_chars=tail_chars,
        max_log_chars=max_log_chars,
    )
    if log_path is not None:
        output.open_log(log_path)
    stdout_task = asyncio.create_task(
        _read_stream(process.stdout, output, "stdout")
    )
    stderr_task = asyncio.create_task(
        _read_stream(process.stderr, output, "stderr")
    )
    return CommandHandle(
        process,
        command=command,
        output=output,
        stdout_task=stdout_task,
        stderr_task=stderr_task,
        started_at=int(time.time() * 1000),
        task_id=task_id,
    )


async def run_command_process(
    command: str,
    *,
    cwd: str,
    timeout_seconds: float,
    max_output_chars: int,
    cancel_event: asyncio.Event | None = None,
    tail_chars: int = 8_000,
    max_log_chars: int = 20_000_000,
) -> CommandResult:
    if not command.strip():
        raise ValueError("command must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    handle = await start_command_process(
        command,
        cwd=cwd,
        max_output_chars=max_output_chars,
        tail_chars=tail_chars,
        max_log_chars=max_log_chars,
    )
    try:
        outcome = await handle.wait_foreground(
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
        )
    except BaseException:
        await handle.terminate()
        await handle.drain_readers(timeout=5.0)
        handle.close_log(delete=True)
        raise
    await handle.drain_readers(timeout=5.0)
    handle.close_log(delete=True)
    return handle.foreground_result(outcome)


__all__ = [
    "CommandHandle",
    "CommandOutput",
    "CommandResult",
    "ForegroundOutcome",
    "start_command_process",
    "run_command_process",
]

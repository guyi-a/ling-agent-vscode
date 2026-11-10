"""Bounded asynchronous command execution."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..config import HarnessConfig
from ..workspace import Workspace


@dataclass(slots=True)
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False

    def to_tool_execution(self) -> ToolExecution:
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
        )


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


async def _read_stream(
    stream: asyncio.StreamReader,
    budget: _OutputBudget,
) -> tuple[str, bool]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    parts: list[str] = []
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        selected, dropped = budget.take(decoder.decode(chunk))
        if selected:
            parts.append(selected)
        truncated = truncated or dropped

    tail = decoder.decode(b"", final=True)
    if tail:
        selected, dropped = budget.take(tail)
        if selected:
            parts.append(selected)
        truncated = truncated or dropped
    return "".join(parts), truncated


async def _wait_for_exit(
    process: asyncio.subprocess.Process,
    timeout_seconds: float,
    cancel_event: asyncio.Event | None,
) -> str | None:
    wait_task = asyncio.create_task(process.wait())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
    cancel_task = (
        asyncio.create_task(cancel_event.wait())
        if cancel_event is not None
        else None
    )
    tasks = {wait_task, timeout_task}
    if cancel_task is not None:
        tasks.add(cancel_task)

    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    if wait_task in done:
        result: str | None = None
    elif cancel_task is not None and cancel_task in done:
        result = "cancelled"
    else:
        result = "timeout"

    wait_task.cancel()
    timeout_task.cancel()
    if cancel_task is not None:
        cancel_task.cancel()
    for task in (wait_task, timeout_task, cancel_task):
        if task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await task
    return result


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


async def run_command_process(
    command: str,
    *,
    cwd: str,
    timeout_seconds: float,
    max_output_chars: int,
    cancel_event: asyncio.Event | None = None,
) -> CommandResult:
    if not command.strip():
        raise ValueError("command must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

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
    budget = _OutputBudget(max_output_chars)
    stdout_task = asyncio.create_task(_read_stream(process.stdout, budget))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, budget))

    termination: str | None
    try:
        termination = await _wait_for_exit(
            process,
            timeout_seconds,
            cancel_event,
        )
        if termination is not None:
            await _terminate_process_tree(process)
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
            stdout_task,
            stderr_task,
        )
    except BaseException:
        await _terminate_process_tree(process)
        stdout_task.cancel()
        stderr_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(stdout_task, stderr_task)
        raise

    return CommandResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=process.returncode,
        timed_out=termination == "timeout",
        cancelled=termination == "cancelled",
        truncated=stdout_truncated or stderr_truncated,
    )


def _optional_timeout(call: ToolCall, default: float) -> float:
    value: Any = call.arguments.get("timeout_seconds", default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_seconds must be a number")
    if value <= 0:
        raise ValueError("timeout_seconds must be positive")
    return float(value)


def create_command_tools(
    workspace: Workspace,
    config: HarnessConfig | None = None,
) -> list[AgentTool]:
    resolved_config = config or workspace.config

    async def run_command(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        command = call.arguments.get("command")
        if not isinstance(command, str):
            raise TypeError("command must be a string")
        requested_timeout = _optional_timeout(
            call,
            resolved_config.command_timeout_seconds,
        )
        timeout_seconds = min(
            requested_timeout,
            resolved_config.command_timeout_seconds,
        )
        result = await run_command_process(
            command,
            cwd=str(workspace.root),
            timeout_seconds=timeout_seconds,
            max_output_chars=resolved_config.max_command_output_chars,
            cancel_event=context.cancel_event,
        )
        return result.to_tool_execution()

    return [
        AgentTool(
            definition=Tool(
                name="run_command",
                description=(
                    "Run a PowerShell command with the workspace as cwd. "
                    "Requires approval. Output and runtime are bounded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_seconds": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            ),
            handler=run_command,
        )
    ]


__all__ = [
    "CommandResult",
    "create_command_tools",
    "run_command_process",
]

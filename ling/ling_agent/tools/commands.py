"""Bounded asynchronous command execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..command_process import (
    CommandCallState,
    CommandResult,
    run_command_process,
    start_command_process,
)
from ..config import HarnessConfig
from ..workspace import Workspace

if TYPE_CHECKING:
    from ..command_calls import CommandCallRegistry


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
    *,
    command_calls: "CommandCallRegistry | None" = None,
) -> list[AgentTool]:
    resolved_config = config or workspace.config

    async def run_command(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        command = call.arguments.get("command")
        if not isinstance(command, str):
            raise TypeError("command must be a string")
        background = call.arguments.get("background", False)
        if not isinstance(background, bool):
            raise TypeError("background must be a boolean")
        if background and (
            command_calls is None or not command_calls.available
        ):
            raise RuntimeError(
                "Background commands require an active session."
            )
        requested_timeout = _optional_timeout(
            call,
            resolved_config.command_timeout_seconds,
        )
        timeout_seconds = min(
            requested_timeout,
            resolved_config.command_timeout_seconds,
        )

        if command_calls is None or not command_calls.available:
            result = await run_command_process(
                command,
                cwd=str(workspace.root),
                timeout_seconds=timeout_seconds,
                max_output_chars=resolved_config.max_command_output_chars,
                cancel_event=context.cancel_event,
            )
            return result.to_tool_execution()

        task_id, log_path = command_calls.allocate()
        handle = await start_command_process(
            command,
            cwd=str(workspace.root),
            max_output_chars=resolved_config.max_command_output_chars,
            tail_chars=resolved_config.background_command_tail_chars,
            max_log_chars=resolved_config.background_command_log_max_chars,
            task_id=task_id,
            log_path=log_path,
        )
        slot = command_calls.register(
            call.id,
            command,
            task_id,
            log_path,
            handle,
        )
        if background:
            from ..command_calls import CommandNotRunning

            try:
                task = await command_calls.request_detach(call.id)
            except CommandNotRunning:
                if (
                    command_calls.state(call.id)
                    is not CommandCallState.FINISHED
                ):
                    raise
                outcome = await handle.wait_foreground(
                    timeout_seconds=0.1,
                )
                await handle.drain_readers(timeout=5.0)
                handle.close_log(delete=True)
                command_calls.complete_foreground(call.id)
                return handle.foreground_result(outcome).to_tool_execution()
            return handle.background_result(task.id).to_tool_execution()

        try:
            outcome = await handle.wait_foreground(
                timeout_seconds=timeout_seconds,
                cancel_event=context.cancel_event,
                detach_event=slot.detach_event,
            )
        except BaseException:
            if command_calls.mark_terminating(call.id):
                await handle.terminate()
                await handle.drain_readers(timeout=5.0)
                handle.close_log(delete=True)
            raise

        state = command_calls.complete_foreground(call.id)
        if state in (
            CommandCallState.DETACHING,
            CommandCallState.DETACHED,
            CommandCallState.TERMINATING,
        ):
            detach = await command_calls.background_outcome(call.id)
            if detach is not None and detach.task is not None:
                handle.flush_log()
                return handle.background_result(task_id).to_tool_execution()
            await handle.drain_readers(timeout=5.0)
            handle.close_log(delete=True)
            error = (
                detach.error
                if detach is not None and detach.error
                else "The command was stopped."
            )
            return ToolExecution(
                output=f"Backgrounding failed: {error}",
                is_error=True,
                details={
                    "kind": "command",
                    "mode": "foreground",
                    "exitCode": None,
                    "error": error,
                },
            )

        await handle.drain_readers(timeout=5.0)
        handle.close_log(delete=True)
        return handle.foreground_result(outcome).to_tool_execution()

    return [
        AgentTool(
            definition=Tool(
                name="run_command",
                description=(
                    "Run a PowerShell command with the workspace as cwd. "
                    "Requires approval. Set background=true for downloads, "
                    "installs, builds, or servers that may outlive the turn; "
                    "it returns a task id immediately and the command keeps "
                    "running after the run ends. Foreground commands are "
                    "bounded by the configured Command timeout; a timeout "
                    "stops the process tree and may leave a file the command "
                    "was writing incomplete. Inspect it before retrying. "
                    "Write long output to a log and read the tail instead of "
                    "piping it through a filter that hides progress or "
                    "errors. Only describe a command as background when the "
                    "result says mode: background."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_seconds": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                        },
                        "background": {"type": "boolean"},
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

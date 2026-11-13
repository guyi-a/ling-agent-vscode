"""Subagent tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Awaitable, Callable

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

if TYPE_CHECKING:
    from ..storage import BackgroundTaskRecord
    from ..subagents import SubagentRunner

SubmitBackground = Callable[
    [str, str, ToolCall],
    Awaitable["BackgroundTaskRecord"],
]


def _string_arg(call: ToolCall, name: str) -> str:
    value = call.arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def create_subagent_tools(
    runner: "SubagentRunner",
    *,
    submit_background: SubmitBackground | None = None,
) -> list[AgentTool]:
    async def spawn_subagent(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        description = _string_arg(call, "description")
        prompt = _string_arg(call, "prompt")
        subagent_type = call.arguments.get("subagent_type", "explore")
        if subagent_type != "explore":
            raise ValueError("subagent_type must be 'explore'")
        run_in_background = call.arguments.get("run_in_background", False)
        if not isinstance(run_in_background, bool):
            raise TypeError("run_in_background must be a boolean")

        if run_in_background:
            if submit_background is None:
                raise RuntimeError("Background subagents are not available")
            task = await submit_background(description, prompt, call)
            return ToolExecution(
                output=json.dumps(
                    {
                        "mode": "background",
                        "subagentType": "explore",
                        "description": description,
                        "taskId": task.id,
                        "status": task.status,
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "subagent",
                    "description": description,
                    "subagentType": "explore",
                    "mode": "background",
                    "taskId": task.id,
                    "status": task.status,
                    "summary": "",
                },
            )

        result = await runner.spawn_explore(
            description,
            prompt,
            call_id=call.id,
            cancel_event=context.cancel_event,
        )
        return ToolExecution(
            output=json.dumps(
                {
                    "mode": "foreground",
                    "subagentType": "explore",
                    "description": description,
                    "summary": result.summary,
                },
                ensure_ascii=False,
            ),
            details={
                "kind": "subagent",
                "description": description,
                "subagentType": "explore",
                "mode": "foreground",
                "summary": result.summary,
                "trace": list(result.trace),
            },
        )

    return [
        AgentTool(
            definition=Tool(
                name="spawn_subagent",
                description=(
                    "Delegate a focused, read-only codebase research task to "
                    "an isolated explore agent. Use when the research would "
                    "require reading many files and the intermediate output "
                    "would clutter the main conversation. The prompt must be "
                    "self-contained. Set run_in_background=true for research "
                    "that can run in parallel; the user is notified when it "
                    "finishes. Do not use it for edits, commands, or user "
                    "decisions; read critical files yourself before editing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "A short user-facing description of the "
                                "delegated research."
                            ),
                        },
                        "prompt": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "A self-contained task with the goal, "
                                "relevant paths, and what the summary must "
                                "answer."
                            ),
                        },
                        "subagent_type": {
                            "type": "string",
                            "enum": ["explore"],
                            "default": "explore",
                        },
                        "run_in_background": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Run the subagent in the background and "
                                "return a task id immediately. Use for "
                                "parallel research that can finish later."
                            ),
                        },
                    },
                    "required": ["description", "prompt"],
                    "additionalProperties": False,
                },
            ),
            handler=spawn_subagent,
        )
    ]


__all__ = ["create_subagent_tools"]

"""Subagent tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

if TYPE_CHECKING:
    from ..subagents import SubagentRunner


def _string_arg(call: ToolCall, name: str) -> str:
    value = call.arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def create_subagent_tools(
    runner: "SubagentRunner",
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
                    "self-contained. Do not use it for edits, commands, or "
                    "user decisions; read critical files yourself before "
                    "editing."
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
                    },
                    "required": ["description", "prompt"],
                    "additionalProperties": False,
                },
            ),
            handler=spawn_subagent,
        )
    ]


__all__ = ["create_subagent_tools"]

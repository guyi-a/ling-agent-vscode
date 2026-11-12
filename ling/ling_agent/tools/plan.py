"""Plan and todo tools used in plan mode."""

from __future__ import annotations

import json
from typing import Any

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..plans import PlanStore

TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")


def _plan_arg(call: ToolCall) -> str:
    value = call.arguments.get("plan")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("plan must be a non-empty string")
    return value.strip()


def _todo_list(call: ToolCall) -> list[dict[str, str]]:
    todos = call.arguments.get("todos")
    if not isinstance(todos, list) or not todos:
        raise ValueError("todos must be a non-empty array")
    parsed: list[dict[str, str]] = []
    in_progress = 0
    for item in todos:
        if not isinstance(item, dict):
            raise ValueError("each todo must be an object")
        todo_id = item.get("id")
        content = item.get("content")
        status = item.get("status", "pending")
        if not isinstance(todo_id, str) or not todo_id.strip():
            raise ValueError("each todo requires a non-empty id")
        if not isinstance(content, str):
            raise ValueError("each todo requires content")
        if status not in TODO_STATUSES:
            raise ValueError(
                "todo status must be one of: "
                + ", ".join(TODO_STATUSES)
            )
        if status == "in_progress":
            in_progress += 1
        parsed.append(
            {
                "id": todo_id.strip(),
                "content": content.strip(),
                "status": status,
            }
        )
    if in_progress > 1:
        raise ValueError("at most one todo may be in_progress")
    return parsed


def create_plan_tools(plans: PlanStore) -> list[AgentTool]:
    def create_plan(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        text = _plan_arg(call)
        path = plans.create(text)
        relative = plans.relative(path)
        return ToolExecution(
            output=json.dumps(
                {"path": relative, "status": "ready"},
                ensure_ascii=False,
            ),
            details={
                "kind": "plan",
                "path": relative,
                "text": text,
            },
        )

    def update_plan(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        text = _plan_arg(call)
        path = plans.update(text)
        relative = plans.relative(path)
        return ToolExecution(
            output=json.dumps(
                {"path": relative, "status": "ready"},
                ensure_ascii=False,
            ),
            details={
                "kind": "plan",
                "path": relative,
                "text": text,
            },
        )

    def todo_write(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        merge = call.arguments.get("merge", True)
        if not isinstance(merge, bool):
            raise ValueError("merge must be a boolean")
        todos = _todo_list(call)
        lines = [
            f"- [{todo['status']}] {todo['id']}: {todo['content']}"
            for todo in todos
        ]
        return ToolExecution(
            output=f"Updated {len(todos)} todo(s):\n" + "\n".join(lines),
            details={"kind": "todo", "todos": todos},
        )

    return [
        AgentTool(
            definition=Tool(
                name="create_plan",
                description=(
                    "Save a markdown implementation plan under "
                    ".agents/plans for the user to review. Use this in plan "
                    "mode, then stop and wait for the user to build the plan."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "plan": {"type": "string", "minLength": 1},
                    },
                    "required": ["plan"],
                    "additionalProperties": False,
                },
            ),
            handler=create_plan,
        ),
        AgentTool(
            definition=Tool(
                name="update_plan",
                description=(
                    "Revise the current plan file during the same planning "
                    "session."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "plan": {"type": "string", "minLength": 1},
                    },
                    "required": ["plan"],
                    "additionalProperties": False,
                },
            ),
            handler=update_plan,
        ),
        AgentTool(
            definition=Tool(
                name="todo_write",
                description=(
                    "Create or update the task checklist for the current "
                    "execution run. Keep at most one item in progress."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "merge": {
                            "type": "boolean",
                            "default": True,
                        },
                        "todos": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "content": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": list(TODO_STATUSES),
                                    },
                                },
                                "required": ["id", "content", "status"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["todos"],
                    "additionalProperties": False,
                },
            ),
            handler=todo_write,
        ),
    ]


__all__ = ["create_plan_tools"]

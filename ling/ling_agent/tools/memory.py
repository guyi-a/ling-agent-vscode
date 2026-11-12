"""Tools for durable project and global memory."""

from __future__ import annotations

import json
from typing import Any, Literal

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..memory import MemoryStore, ProjectMemory


def _string_arg(call: ToolCall, name: str) -> str:
    value = call.arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _scope(call: ToolCall) -> Literal["global", "workspace"]:
    value = call.arguments.get("scope", "workspace")
    if value not in ("global", "workspace"):
        raise ValueError("scope must be 'workspace' or 'global'")
    return value


def _bool_arg(call: ToolCall, name: str, default: bool) -> bool:
    value = call.arguments.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _globs(call: ToolCall) -> tuple[str, ...]:
    value = call.arguments.get("globs")
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("globs must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _memory_json(memory: ProjectMemory) -> dict[str, Any]:
    return {
        "name": memory.name,
        "description": memory.description,
        "content": memory.body,
        "scope": memory.scope,
        "alwaysApply": memory.always_apply,
        "globs": list(memory.globs),
        "path": memory.relative_path,
    }


def _error(call: ToolCall, exc: Exception) -> ToolExecution:
    message = str(exc)
    return ToolExecution(
        output=json.dumps(
            {
                "ok": False,
                "code": getattr(exc, "code", "INVALID_ARGUMENT"),
                "message": message,
            },
            ensure_ascii=False,
        ),
        is_error=True,
        details={
            "kind": "text",
            "text": message,
        },
    )


def create_memory_tools(store: MemoryStore) -> list[AgentTool]:
    def memory_list(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        try:
            requested = call.arguments.get("scope", "all")
            if requested not in ("all", "workspace", "global"):
                raise ValueError(
                    "scope must be 'all', 'workspace', or 'global'"
                )
            result = store.list()
            memories = [
                _memory_json(memory)
                for memory in result.memories
                if requested == "all" or memory.scope == requested
            ]
            return ToolExecution(
                output=json.dumps(
                    {
                        "ok": True,
                        "memories": memories,
                        "diagnostics": list(result.diagnostics),
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "text",
                    "text": "\n".join(
                        [
                            f"{memory['scope']}: {memory['name']}"
                            for memory in memories
                        ]
                        + list(result.diagnostics)
                    )
                    or "No memories",
                },
            )
        except ValueError as exc:
            return _error(call, exc)

    def memory_write(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        try:
            name = _string_arg(call, "name")
            description = _string_arg(call, "description")
            content = _string_arg(call, "content")
            scope = _scope(call)
            always_apply = _bool_arg(call, "always_apply", True)
            globs = _globs(call)
            if not always_apply and not globs:
                raise ValueError(
                    "inactive memory requires at least one glob"
                )
            path = store.write(
                name=name,
                description=description,
                content=content,
                scope=scope,
                always_apply=always_apply,
                globs=globs,
            )
            return ToolExecution(
                output=json.dumps(
                    {
                        "ok": True,
                        "scope": scope,
                        "name": name,
                        "path": str(path),
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "text",
                    "text": f"Wrote {scope} memory {name}",
                },
            )
        except ValueError as exc:
            return _error(call, exc)

    def memory_delete(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        try:
            if call.arguments.get("confirm") is not True:
                raise ValueError(
                    "confirm must be true to delete a memory"
                )
            name = _string_arg(call, "name")
            scope = _scope(call)
            path = store.delete(name=name, scope=scope)
            return ToolExecution(
                output=json.dumps(
                    {
                        "ok": True,
                        "scope": scope,
                        "name": name,
                        "path": str(path),
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "text",
                    "text": f"Deleted {scope} memory {name}",
                },
            )
        except ValueError as exc:
            return _error(call, exc)

    return [
        AgentTool(
            definition=Tool(
                name="memory_list",
                description=(
                    "List durable workspace and global memories, including "
                    "activation state, globs, paths, and diagnostics."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["all", "workspace", "global"],
                            "default": "all",
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            handler=memory_list,
        ),
        AgentTool(
            definition=Tool(
                name="memory_write",
                description=(
                    "Create or replace a durable memory. Use workspace scope "
                    "for project knowledge and global scope only for stable "
                    "cross-project preferences. This writes a normal Markdown "
                    "memory file and requires approval."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                        },
                        "description": {"type": "string"},
                        "content": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["workspace", "global"],
                            "default": "workspace",
                        },
                        "always_apply": {
                            "type": "boolean",
                            "default": True,
                        },
                        "globs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "name",
                        "description",
                        "content",
                    ],
                    "additionalProperties": False,
                },
            ),
            handler=memory_write,
        ),
        AgentTool(
            definition=Tool(
                name="memory_delete",
                description=(
                    "Delete a durable memory by name and scope. Requires both "
                    "confirm=true and the normal write approval."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["workspace", "global"],
                        },
                        "confirm": {
                            "type": "boolean",
                            "const": True,
                        },
                    },
                    "required": ["name", "scope", "confirm"],
                    "additionalProperties": False,
                },
            ),
            handler=memory_delete,
        ),
    ]


__all__ = ["create_memory_tools"]

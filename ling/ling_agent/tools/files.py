"""Read-only file tools."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext

from ..workspace import Workspace
from .formatting import join_limited


def _string_arg(call: ToolCall, name: str, default: str | None = None) -> str:
    value = call.arguments.get(name, default)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _positive_optional_int(call: ToolCall, name: str) -> int | None:
    value = call.arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def create_file_tools(
    workspace: Workspace,
    *,
    extra_read_roots: Sequence[Path] = (),
) -> list[AgentTool]:
    def read_file(call: ToolCall, context: ToolContext) -> str:
        path = _string_arg(call, "path")
        start_line = _positive_optional_int(call, "start_line") or 1
        line_limit = _positive_optional_int(call, "line_limit")
        if Path(path).is_absolute():
            return workspace.read_absolute_text(
                path,
                allowed_roots=extra_read_roots,
                start_line=start_line,
                line_limit=line_limit,
            )
        return workspace.read_text(
            path,
            start_line=start_line,
            line_limit=line_limit,
        )

    def list_files(call: ToolCall, context: ToolContext) -> str:
        path = _string_arg(call, "path", ".")
        recursive = call.arguments.get("recursive", False)
        if not isinstance(recursive, bool):
            raise TypeError("recursive must be a boolean")
        entries = workspace.list_files(path, recursive=recursive)
        return join_limited(
            entries,
            workspace.config.max_tool_result_chars,
            "(no files)",
        )

    return [
        AgentTool(
            definition=Tool(
                name="read_file",
                description=(
                    "Read a UTF-8 text file. Prefer workspace-relative paths; "
                    "absolute paths are allowed inside the workspace or "
                    "bundled skill directories."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "line_limit": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            handler=read_file,
        ),
        AgentTool(
            definition=Tool(
                name="list_files",
                description="List files and directories inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "recursive": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
            ),
            handler=list_files,
        ),
    ]


def create_mutation_tools(workspace: Workspace) -> list[AgentTool]:
    def write_file(call: ToolCall, context: ToolContext) -> str:
        path = _string_arg(call, "path")
        content = _string_arg(call, "content")
        return workspace.write_text(path, content)

    def edit_file(call: ToolCall, context: ToolContext) -> str:
        path = _string_arg(call, "path")
        old_text = _string_arg(call, "old_text")
        new_text = _string_arg(call, "new_text")
        return workspace.replace_text(path, old_text, new_text)

    return [
        AgentTool(
            definition=Tool(
                name="write_file",
                description=(
                    "Create or replace a UTF-8 text file inside the workspace. "
                    "Requires approval."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            handler=write_file,
        ),
        AgentTool(
            definition=Tool(
                name="edit_file",
                description=(
                    "Replace one exact text occurrence in a workspace file. "
                    "Requires approval."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            ),
            handler=edit_file,
        ),
    ]


__all__ = ["create_file_tools", "create_mutation_tools"]

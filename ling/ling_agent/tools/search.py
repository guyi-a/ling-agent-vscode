"""Read-only search tools."""

from __future__ import annotations

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


def create_search_tools(workspace: Workspace) -> list[AgentTool]:
    def glob_files(call: ToolCall, context: ToolContext) -> str:
        pattern = _string_arg(call, "pattern")
        matches = workspace.glob(pattern)
        return join_limited(
            matches,
            workspace.config.max_tool_result_chars,
            "(no matches)",
        )

    def grep_files(call: ToolCall, context: ToolContext) -> str:
        pattern = _string_arg(call, "pattern")
        path = _string_arg(call, "path", ".")
        max_matches = _positive_optional_int(call, "max_matches")
        matches = workspace.grep(pattern, path, max_matches=max_matches)
        return join_limited(
            matches,
            workspace.config.max_tool_result_chars,
            "(no matches)",
        )

    return [
        AgentTool(
            definition=Tool(
                name="glob",
                description="Find workspace files matching a glob pattern.",
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            ),
            handler=glob_files,
        ),
        AgentTool(
            definition=Tool(
                name="grep",
                description=(
                    "Search text files in the workspace using a Python regular "
                    "expression."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "max_matches": {"type": "integer", "minimum": 1},
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            ),
            handler=grep_files,
        ),
    ]


__all__ = ["create_search_tools"]

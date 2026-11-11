"""Read-only search tools."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext

from ..workspace import Workspace
from .formatting import join_limited

if TYPE_CHECKING:
    from ling_retrieval.embedding import Embedder


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


def _optional_string(call: ToolCall, name: str) -> str | None:
    value = call.arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def create_search_tools(
    workspace: Workspace,
    *,
    embedder: "Embedder | None" = None,
) -> list[AgentTool]:
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

    async def search_codebase(
        call: ToolCall,
        context: ToolContext,
    ) -> str:
        query = _string_arg(call, "query")
        top_k = _positive_optional_int(call, "top_k") or 8
        language = _optional_string(call, "language")
        path_prefix = _optional_string(call, "path_prefix")

        from ling_retrieval.index import CodeIndex

        index = CodeIndex(workspace.root)
        results = await asyncio.to_thread(
            index.search,
            query,
            top_k=top_k,
            language=language,
            path_prefix=path_prefix,
            embedder=embedder,
        )
        payload: dict[str, object] = {
            "query": query,
            "mode": index.last_search_mode,
            "results": results,
        }
        if not results:
            payload["message"] = (
                "No matching code found. Try different terms or use grep."
            )
        return json.dumps(payload, ensure_ascii=False)

    return [
        AgentTool(
            definition=Tool(
                name="search_codebase",
                description=(
                    "Find relevant functions, methods, and classes by concept "
                    "or behavior. Returns ranked chunks with path, line "
                    "range, signature, comment, and a short preview. Use grep "
                    "for exact strings and read_file for a known path; read "
                    "the target file before editing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "top_k": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 8,
                        },
                        "language": {
                            "type": "string",
                            "enum": [
                                "python",
                                "typescript",
                                "javascript",
                            ],
                        },
                        "path_prefix": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            handler=search_codebase,
        ),
        AgentTool(
            definition=Tool(
                name="glob",
                description=(
                    "Find workspace files matching a glob pattern. Common "
                    "build and vendor directories are skipped."
                ),
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
                    "expression. Common build and vendor directories are "
                    "skipped."
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

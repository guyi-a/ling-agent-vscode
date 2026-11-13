"""Read-only file tools."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..file_state import FileReadTracker
from ..workspace import Workspace, WorkspaceError
from .formatting import join_limited

MAX_DETAIL_FILE_BYTES = 100_000


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
    file_reads: FileReadTracker | None = None,
) -> list[AgentTool]:
    def read_file(call: ToolCall, context: ToolContext) -> str:
        path = _string_arg(call, "path")
        start_line = _positive_optional_int(call, "start_line") or 1
        line_limit = _positive_optional_int(call, "line_limit")
        if Path(path).is_absolute():
            resolved = Path(path).expanduser().resolve()
            output = workspace.read_absolute_text(
                path,
                allowed_roots=extra_read_roots,
                start_line=start_line,
                line_limit=line_limit,
            )
        else:
            resolved = workspace.resolve(path)
            output = workspace.read_text(
                path,
                start_line=start_line,
                line_limit=line_limit,
            )
        if file_reads is not None:
            file_reads.mark_read(resolved)
        return output

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


def create_mutation_tools(
    workspace: Workspace,
    *,
    file_reads: FileReadTracker | None = None,
) -> list[AgentTool]:
    def write_file(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        path = _string_arg(call, "path")
        content = _string_arg(call, "content")
        resolved = workspace.resolve(path)
        if file_reads is not None and resolved.exists():
            issue = file_reads.check_editable(resolved, label=path)
            if issue is not None:
                return ToolExecution(output=issue, is_error=True)
        previous, existed = _previous_content(workspace, path)
        output = workspace.write_text(path, content)
        if file_reads is not None:
            file_reads.mark_written(resolved)
        return ToolExecution(
            output=output,
            details={
                "kind": "write",
                "previousContent": previous,
                "previousExists": existed,
                "previousTruncated": existed and previous is None,
            },
        )

    def edit_file(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        path = _string_arg(call, "path")
        old_text = _string_arg(call, "old_text")
        new_text = _string_arg(call, "new_text")
        resolved = workspace.resolve(path)
        if file_reads is not None:
            issue = file_reads.check_editable(resolved, label=path)
            if issue is not None:
                return ToolExecution(output=issue, is_error=True)
        start_line = _find_start_line(workspace, path, old_text)
        output = workspace.replace_text(path, old_text, new_text)
        if file_reads is not None:
            file_reads.mark_written(resolved)
        return ToolExecution(
            output=output,
            details={"kind": "edit", "startLine": start_line},
        )

    return [
        AgentTool(
            definition=Tool(
                name="write_file",
                description=(
                    "Create or replace a UTF-8 text file inside the workspace. "
                    "Replacing an existing file requires reading it first. "
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
                    "The file must be read first. Requires approval."
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


def _previous_content(
    workspace: Workspace,
    path: str,
) -> tuple[str | None, bool]:
    try:
        resolved = workspace.resolve(path)
        if not resolved.is_file():
            return None, False
        if resolved.stat().st_size > MAX_DETAIL_FILE_BYTES:
            return None, True
        return resolved.read_text(encoding="utf-8"), True
    except (OSError, UnicodeDecodeError, WorkspaceError):
        return None, False


def _find_start_line(
    workspace: Workspace,
    path: str,
    old_text: str,
) -> int | None:
    try:
        resolved = workspace.resolve(path)
        if not resolved.is_file():
            return None
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, WorkspaceError):
        return None
    index = text.find(old_text)
    if index < 0:
        return None
    return text.count("\n", 0, index) + 1


__all__ = ["create_file_tools", "create_mutation_tools"]

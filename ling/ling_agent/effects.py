"""Structured risk effects derived from tool calls."""

from __future__ import annotations

import re
import shlex
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TypeAlias

from ling_ai.types import ToolCall

from .browser_bridge import classify_browser_action
from .memory import default_global_memory_root
from .web import is_private_web_target
from .workspace import Workspace, WorkspacePathError

CommandClassification: TypeAlias = Literal["harmless", "normal", "destructive"]


class ApprovalMode(str, Enum):
    MANUAL = "manual"
    ACCEPT_WRITE = "accept-write"
    AUTO = "auto"
    PLAN = "plan"


@dataclass(slots=True, frozen=True)
class FilesystemReadEffect:
    operation: Literal["read", "list", "grep", "glob"]
    path: Path
    scope: Literal["workspace", "absolute"] = "workspace"
    kind: Literal["filesystem_read"] = "filesystem_read"


@dataclass(slots=True, frozen=True)
class FilesystemWriteEffect:
    operation: Literal["write", "edit"]
    path: Path
    scope: Literal["workspace", "global"] = "workspace"
    kind: Literal["filesystem_write"] = "filesystem_write"


@dataclass(slots=True, frozen=True)
class ProcessExecEffect:
    command: str
    cwd: Path
    classification: CommandClassification
    kind: Literal["process_exec"] = "process_exec"


@dataclass(slots=True, frozen=True)
class NetworkEffect:
    operation: Literal["fetch", "search"]
    target: str
    private_host: bool
    kind: Literal["network"] = "network"


@dataclass(slots=True, frozen=True)
class MetaEffect:
    operation: str
    kind: Literal["meta"] = "meta"


@dataclass(slots=True, frozen=True)
class SubagentEffect:
    readonly: Literal[True] = True
    kind: Literal["subagent"] = "subagent"


@dataclass(slots=True, frozen=True)
class BrowserEffect:
    action: str
    classification: Literal["readonly", "mutating", "script"]
    target: str = ""
    kind: Literal["browser"] = "browser"


@dataclass(slots=True, frozen=True)
class ComputerEffect:
    operation: Literal[
        "list",
        "observe",
        "screenshot",
        "focus",
        "act",
        "launch",
        "stop",
    ]
    classification: Literal["readonly", "mutating", "launch"]
    window_id: str = ""
    app: str = ""
    action: str = ""
    kind: Literal["computer_use"] = "computer_use"


@dataclass(slots=True, frozen=True)
class McpEffect:
    server: str
    tool: str
    transport: Literal["http", "stdio"]
    target: str
    private_host: bool
    read_only: bool
    destructive: bool
    open_world: bool
    trusted_annotations: bool
    kind: Literal["mcp_call"] = "mcp_call"


@dataclass(slots=True, frozen=True)
class McpAdminEffect:
    operation: str
    server: str | None = None
    kind: Literal["mcp_admin"] = "mcp_admin"


@dataclass(slots=True, frozen=True)
class UnknownEffect:
    note: str
    kind: Literal["unknown"] = "unknown"


Effect: TypeAlias = (
    FilesystemReadEffect
    | FilesystemWriteEffect
    | ProcessExecEffect
    | NetworkEffect
    | MetaEffect
    | SubagentEffect
    | BrowserEffect
    | ComputerEffect
    | McpEffect
    | McpAdminEffect
    | UnknownEffect
)

_SHELL_OPERATOR = re.compile(r"[;&|<>`]|(?:\$\()")
_DESTRUCTIVE_COMMANDS = (
    re.compile(r"(?i)\bRemove-Item\b"),
    re.compile(r"(?i)\b(?:rd|rmdir)\s+/s\b"),
    re.compile(r"(?i)\bdel\s+/[a-z]*s\b"),
    re.compile(r"(?i)\bgit\s+reset\s+--hard\b"),
    re.compile(r"(?i)\bgit\s+clean\s+-\S*f"),
    re.compile(r"(?i)\b(?:Format-Volume|Clear-Disk|Stop-Computer|Restart-Computer|diskpart)\b"),
    re.compile(
        r"(?i)(?:^|[;&|]\s*)format(?:\.com)?(?=\s|$)"
    ),
    re.compile(r"(?i)\bshutdown\b"),
)
_HARMLESS_SIMPLE = {
    "get-location",
    "pwd",
}
_READONLY_LIST = {"get-childitem", "dir", "ls"}
_READONLY_READ = {"get-content", "cat", "type"}
_SAFE_GIT_SUBCOMMANDS = {"status", "diff", "log", "show"}
_SENSITIVE_PATH = re.compile(
    r"(?:^|[\\/])\.env(?:\.|$)|\.(?:pem|key)$|credential|secret|token|password",
    re.IGNORECASE,
)
_SENSITIVE_CONTENT = re.compile(
    r"(?:api[_-]?key|secret|token|password)\s*[:=]",
    re.IGNORECASE,
)


def _tokens(command: str) -> list[str]:
    try:
        return [
            token.strip("\"'")
            for token in shlex.split(command, posix=False)
            if token.strip("\"'")
        ]
    except ValueError:
        return []


def _path_tokens(tokens: list[str]) -> list[str]:
    return [
        token
        for token in tokens
        if not token.startswith("-") and not token.startswith("/")
    ]


def _all_paths_inside(tokens: list[str], workspace: Workspace | None) -> bool:
    paths = _path_tokens(tokens)
    if workspace is None:
        return not paths
    for path in paths:
        try:
            workspace.resolve(path)
        except WorkspacePathError:
            return False
    return True


def classify_command(
    command: str,
    workspace: Workspace | None = None,
) -> CommandClassification:
    value = command.strip()
    if not value:
        return "normal"
    if any(pattern.search(value) for pattern in _DESTRUCTIVE_COMMANDS):
        return "destructive"
    if _SHELL_OPERATOR.search(value) or "$" in value or "\n" in value:
        return "normal"

    tokens = _tokens(value)
    if not tokens:
        return "normal"
    executable = tokens[0].lower()

    if executable in _HARMLESS_SIMPLE:
        return "harmless" if len(tokens) == 1 else "normal"

    if executable in _READONLY_LIST:
        return "harmless" if _all_paths_inside(tokens[1:], workspace) else "normal"

    if executable in _READONLY_READ:
        if not _all_paths_inside(tokens[1:], workspace):
            return "normal"
        path_args = _path_tokens(tokens[1:])
        return "harmless" if path_args else "normal"

    if executable == "git" and len(tokens) >= 2:
        subcommand = tokens[1].lower()
        if subcommand not in _SAFE_GIT_SUBCOMMANDS:
            return "normal"
        if any("--output" in token.lower() for token in tokens[2:]):
            return "normal"
        return "harmless" if _all_paths_inside(tokens[2:], workspace) else "normal"

    return "normal"


def derive_effect(call: ToolCall, workspace: Workspace) -> Effect:
    arguments = call.arguments

    if call.name in ("load_skill", "ask_user", "mcp_list_servers"):
        return MetaEffect(operation=call.name)

    if call.name in (
        "computer_list",
        "computer_observe",
        "computer_screenshot",
        "computer_focus",
        "computer_act",
        "computer_launch",
        "computer_stop",
    ):
        operation = call.name.removeprefix("computer_")
        if operation in ("list", "stop"):
            return ComputerEffect(
                operation=operation,
                classification="readonly",
            )
        window_id = arguments.get("window_id")
        if operation in ("observe", "screenshot", "focus", "act") and (
            not isinstance(window_id, str) or not window_id.strip()
        ):
            return UnknownEffect(
                note="computer window_id is missing or invalid"
            )
        if operation in ("observe", "screenshot"):
            return ComputerEffect(
                operation=operation,
                classification="readonly",
                window_id=window_id,
            )
        if operation == "focus":
            return ComputerEffect(
                operation="focus",
                classification="mutating",
                window_id=window_id,
                action="focus",
            )
        if operation == "act":
            actions = arguments.get("actions")
            if not isinstance(actions, list) or not actions:
                return UnknownEffect(
                    note="computer actions are missing or invalid"
                )
            first = actions[0]
            action = (
                first.get("type", "")
                if isinstance(first, dict)
                else ""
            )
            return ComputerEffect(
                operation="act",
                classification="mutating",
                window_id=window_id,
                action=action if isinstance(action, str) else "",
            )
        app = arguments.get("app")
        if not isinstance(app, str) or not app.strip():
            return UnknownEffect(
                note="computer app is missing or invalid"
            )
        return ComputerEffect(
            operation="launch",
            classification="launch",
            app=app,
        )

    if call.name == "memory_list":
        return MetaEffect(operation="memory_list")

    if call.name in ("memory_write", "memory_delete"):
        name = arguments.get("name")
        scope = arguments.get("scope", "workspace")
        if not isinstance(name, str) or not name:
            return UnknownEffect(note="memory name is missing or invalid")
        if scope not in ("workspace", "global"):
            return UnknownEffect(note="memory scope is missing or invalid")
        root = (
            workspace.root / ".agents" / "memory"
            if scope == "workspace"
            else default_global_memory_root()
        )
        return FilesystemWriteEffect(
            operation="write",
            path=root / f"{name}.md",
            scope=scope,
        )

    if call.name in ("create_plan", "update_plan"):
        return MetaEffect(operation="plan")

    if call.name == "todo_write":
        return MetaEffect(operation="todo")

    if call.name == "spawn_subagent":
        return SubagentEffect()

    if call.name == "browser_bridge":
        action = arguments.get("action")
        if not isinstance(action, str) or not action:
            return UnknownEffect(note="browser action is missing or invalid")
        target = arguments.get("url") or arguments.get("page_id") or ""
        return BrowserEffect(
            action=action,
            classification=classify_browser_action(action),
            target=target if isinstance(target, str) else "",
        )

    if call.name in (
        "mcp_add_server",
        "mcp_remove_server",
        "mcp_set_enabled",
        "mcp_reload",
        "mcp_test_server",
        "mcp_authorize_server",
        "mcp_revoke_server_auth",
    ):
        name = arguments.get("name")
        return McpAdminEffect(
            operation=call.name.removeprefix("mcp_"),
            server=name if isinstance(name, str) and name else None,
        )

    if call.name == "web_search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return UnknownEffect(note="web query is missing or invalid")
        return NetworkEffect(
            operation="search",
            target=query.strip(),
            private_host=False,
        )

    if call.name == "web_fetch":
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            return UnknownEffect(note="web URL is missing or invalid")
        return NetworkEffect(
            operation="fetch",
            target=url.strip(),
            private_host=is_private_web_target(url.strip()),
        )

    if call.name in (
        "read_file",
        "list_files",
        "grep",
        "glob",
        "search_codebase",
        "extract_document_text",
    ):
        raw_path = arguments.get("path", ".")
        if not isinstance(raw_path, str):
            return UnknownEffect(note="read path is missing or invalid")
        operation: Literal["read", "list", "grep", "glob"]
        if call.name in ("read_file", "extract_document_text"):
            operation = "read"
        elif call.name == "list_files":
            operation = "list"
        elif call.name in ("grep", "search_codebase"):
            operation = "grep"
            if call.name == "search_codebase":
                raw_path = "."
        else:
            operation = "glob"
            raw_path = "."

        if call.name == "read_file" and Path(raw_path).is_absolute():
            return FilesystemReadEffect(
                operation=operation,
                path=Path(raw_path).expanduser(),
                scope="absolute",
            )
        if call.name == "extract_document_text" and Path(raw_path).is_absolute():
            return FilesystemReadEffect(
                operation=operation,
                path=Path(raw_path).expanduser(),
                scope="absolute",
            )
        return FilesystemReadEffect(
            operation=operation,
            path=workspace.resolve(raw_path),
        )

    if call.name in ("write_file", "edit_file"):
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str):
            return UnknownEffect(note="write path is missing or invalid")
        return FilesystemWriteEffect(
            operation="write" if call.name == "write_file" else "edit",
            path=workspace.resolve(raw_path),
        )

    if call.name == "run_command":
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return UnknownEffect(note="command is missing or invalid")
        return ProcessExecEffect(
            command=command,
            cwd=workspace.root,
            classification=classify_command(command, workspace),
        )

    return UnknownEffect(note=f"No effect derivation for tool {call.name}")


def effect_digest(effect: Effect, call: ToolCall) -> str:
    payload = {
        "effect": {
            "kind": effect.kind,
            **{
                key: str(value) if isinstance(value, Path) else value
                for key, value in (
                    (field, getattr(effect, field))
                    for field in effect.__dataclass_fields__
                    if field != "kind"
                )
            },
        },
        "tool": call.name,
        "arguments": call.arguments,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sensitive_reason(effect: Effect, call: ToolCall | None) -> str | None:
    if not isinstance(effect, FilesystemWriteEffect):
        return None
    if effect.scope == "global":
        return "global memory"
    if _SENSITIVE_PATH.search(str(effect.path)):
        return "sensitive file"
    if call is None:
        return None
    content = "\n".join(
        value
        for key in ("content", "new_text", "newText")
        if isinstance((value := call.arguments.get(key)), str)
    )
    if content and _SENSITIVE_CONTENT.search(content):
        return "content may contain credentials"
    return None


def plan_mode_denial(effect: Effect) -> str | None:
    """Return a denial message when plan mode blocks an effect."""
    if isinstance(effect, ComputerEffect) and effect.classification != "readonly":
        return (
            "Plan mode is read-only. Computer actions that control the "
            "desktop are blocked; use computer_list or computer_observe."
        )
    if isinstance(effect, BrowserEffect) and effect.classification != "readonly":
        return (
            "Plan mode is read-only. Browser actions that change the page "
            "are blocked; use extension_status, list_pages, read_state, "
            "extract, describe_element, wait_for, or scroll."
        )
    if isinstance(effect, McpAdminEffect):
        return (
            "Plan mode cannot add, remove, enable, disable, or reload MCP "
            "servers because those operations change future capabilities."
        )
    if isinstance(effect, McpEffect):
        trusted_read = (
            effect.read_only
            and effect.trusted_annotations
            and not effect.destructive
        )
        if not trusted_read:
            return (
                "Plan mode only allows MCP tools marked read-only by a "
                "server whose annotations you explicitly trust."
            )
    if isinstance(
        effect,
        (
            FilesystemWriteEffect,
            ProcessExecEffect,
            UnknownEffect,
        ),
    ):
        return (
            "Plan mode is read-only. Present a plan with create_plan "
            "and wait for the user to build it."
        )
    return None


def approval_reason(
    effect: Effect,
    mode: ApprovalMode = ApprovalMode.MANUAL,
    call: ToolCall | None = None,
) -> str | None:
    if isinstance(effect, (MetaEffect, SubagentEffect)):
        return None
    if isinstance(effect, ComputerEffect):
        if effect.classification == "readonly":
            return None
        target = effect.app or effect.window_id or "desktop"
        return f"computer {effect.operation} ({target})"
    if isinstance(effect, BrowserEffect):
        if effect.classification == "readonly":
            return None
        if effect.classification == "script":
            return "browser execute_script"
        return (
            None
            if mode is ApprovalMode.AUTO
            else f"browser {effect.action}"
        )
    if isinstance(effect, McpAdminEffect):
        target = f" {effect.server}" if effect.server else ""
        return f"MCP server {effect.operation}{target}"
    if isinstance(effect, McpEffect):
        if effect.destructive:
            return f"MCP destructive tool {effect.server}/{effect.tool}"
        if effect.transport == "stdio":
            return (
                None
                if mode is ApprovalMode.AUTO
                else f"MCP stdio tool {effect.server}/{effect.tool}"
            )
        if effect.private_host:
            return f"MCP private network tool {effect.server}/{effect.tool}"
        return None
    if isinstance(effect, UnknownEffect):
        return effect.note
    if isinstance(effect, NetworkEffect):
        return "private network URL is blocked" if effect.private_host else None
    if isinstance(effect, FilesystemReadEffect):
        return None

    sensitive = _sensitive_reason(effect, call)
    if sensitive is not None:
        return sensitive

    if isinstance(effect, FilesystemWriteEffect):
        return (
            f"{effect.operation} {effect.path}"
            if mode is ApprovalMode.MANUAL
            else None
        )

    if isinstance(effect, ProcessExecEffect):
        if effect.classification == "destructive":
            return "destructive command"
        if effect.classification == "harmless":
            return None
        return (
            None
            if mode is ApprovalMode.AUTO
            else f"{effect.classification} command"
        )

    return None


__all__ = [
    "CommandClassification",
    "ApprovalMode",
    "BrowserEffect",
    "ComputerEffect",
    "Effect",
    "FilesystemReadEffect",
    "FilesystemWriteEffect",
    "McpAdminEffect",
    "McpEffect",
    "MetaEffect",
    "NetworkEffect",
    "plan_mode_denial",
    "ProcessExecEffect",
    "SubagentEffect",
    "UnknownEffect",
    "approval_reason",
    "classify_command",
    "derive_effect",
    "effect_digest",
]

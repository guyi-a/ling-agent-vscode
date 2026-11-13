"""Agent tools for invoking and managing MCP servers."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from mcp import types
from mcp.shared.exceptions import McpError

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from .config import McpServerConfig
from .manager import (
    McpCancelled,
    McpConnectionError,
    McpManager,
    McpNeedsAuthorization,
    McpStatus,
    McpToolDescriptor,
)


def create_mcp_agent_tools(
    manager: McpManager,
    bindings: list[tuple[McpServerConfig, McpToolDescriptor]],
) -> list[AgentTool]:
    tools: list[AgentTool] = []
    for server, descriptor in bindings:
        async def invoke(
            call: ToolCall,
            context: ToolContext,
            *,
            public_name: str = descriptor.public_name,
            remote_name: str = descriptor.remote_name,
            server_name: str = server.name,
        ) -> ToolExecution:
            try:
                result = await manager.call_tool(
                    public_name,
                    call.arguments,
                    cancel_event=context.cancel_event,
                )
                output = _flatten_call_result(result)
                return ToolExecution(
                    output=output,
                    is_error=bool(result.isError),
                    details={
                        "kind": "io",
                        "input": json.dumps(
                            {
                                "server": server_name,
                                "tool": remote_name,
                                "arguments": call.arguments,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        "output": _preview(output),
                        **(
                            {"error": output}
                            if result.isError
                            else {}
                        ),
                    },
                )
            except McpCancelled as exc:
                return _error_execution(server_name, str(exc))
            except McpError as exc:
                return _error_execution(server_name, str(exc))
            except (McpConnectionError, Exception) as exc:
                return _error_execution(server_name, str(exc))

        tools.append(
            AgentTool(
                definition=Tool(
                    name=descriptor.public_name,
                    description=(
                        f"{descriptor.description}\n\n"
                        f"Provided by MCP server {server.name!r} "
                        f"as tool {descriptor.remote_name!r}."
                    ),
                    parameters=descriptor.input_schema,
                ),
                handler=invoke,
            )
        )
    return tools


async def create_mcp_tools(
    manager: McpManager,
    reserved_names: set[str],
) -> list[AgentTool]:
    """Return MCP invocation tools plus non-dynamic management tools."""

    async def mcp_list_servers(
        _call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        status = await manager.status()
        payload = {
            "ok": True,
            "configPath": status.config_path,
            "servers": [_status_json(server) for server in status.servers],
            "issues": [
                {
                    "name": issue.name,
                    "message": issue.message,
                }
                for issue in status.issues
            ],
        }
        return ToolExecution(
            output=json.dumps(payload, ensure_ascii=False),
            details={"kind": "io", "input": "MCP inventory", "output": _status_summary(status.servers)},
        )

    async def mcp_reload(
        _call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        manager.invalidate_manifests()
        status = await manager.status()
        return ToolExecution(
            output=json.dumps(
                {
                    "ok": True,
                    "message": (
                        "MCP manifests were cleared. Tool changes apply "
                        "on the next model turn."
                    ),
                    "servers": [
                        _status_json(server) for server in status.servers
                    ],
                },
                ensure_ascii=False,
            ),
            details={
                "kind": "io",
                "input": "Reload MCP manifests",
                "output": _status_summary(status.servers),
            },
        )

    async def mcp_test_server(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            name = _string_arg(call, "name")
            status = await manager.test_server(name)
            return ToolExecution(
                output=json.dumps(
                    {"ok": True, "server": _status_json(status)},
                    ensure_ascii=False,
                ),
                details={
                    "kind": "io",
                    "input": name,
                    "output": _status_summary((status,)),
                },
            )
        except Exception as exc:
            return _error_execution("mcp", str(exc))

    async def mcp_add_server(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            server = _server_from_call(call)
            status = await manager.add_server(server)
            return ToolExecution(
                output=json.dumps(
                    {
                        "ok": True,
                        "message": (
                            f"MCP server {server.name!r} was saved. "
                            "Its tools become available on the next turn."
                        ),
                        "configPath": status.config_path,
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "io",
                    "input": server.target,
                    "output": f"Saved MCP server {server.name}",
                },
            )
        except Exception as exc:
            return _error_execution("mcp", str(exc))

    async def mcp_remove_server(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            if call.arguments.get("confirm") is not True:
                raise ValueError("confirm must be true to remove an MCP server")
            name = _string_arg(call, "name")
            await manager.remove_server(name)
            return ToolExecution(
                output=json.dumps(
                    {"ok": True, "removed": name},
                    ensure_ascii=False,
                ),
                details={
                    "kind": "io",
                    "input": name,
                    "output": f"Removed MCP server {name}",
                },
            )
        except Exception as exc:
            return _error_execution("mcp", str(exc))

    async def mcp_set_enabled(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            name = _string_arg(call, "name")
            enabled = call.arguments.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            await manager.set_enabled(name, enabled)
            return ToolExecution(
                output=json.dumps(
                    {"ok": True, "name": name, "enabled": enabled},
                    ensure_ascii=False,
                ),
                details={
                    "kind": "io",
                    "input": name,
                    "output": (
                        f"{'Enabled' if enabled else 'Disabled'} MCP server "
                        f"{name}"
                    ),
                },
            )
        except Exception as exc:
            return _error_execution("mcp", str(exc))

    async def mcp_authorize_server(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            name = _string_arg(call, "name")
            status = await manager.authorize_server(name)
            return ToolExecution(
                output=json.dumps(
                    {
                        "ok": True,
                        "message": (
                            f"OAuth authorization for {name!r} completed. "
                            "MCP tools become available on the next turn."
                        ),
                        "server": _status_json(status),
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "io",
                    "input": name,
                    "output": _status_summary((status,)),
                },
            )
        except Exception as exc:
            return _error_execution("mcp", str(exc))

    async def mcp_revoke_server_auth(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            name = _string_arg(call, "name")
            status = await manager.revoke_server_auth(name)
            return ToolExecution(
                output=json.dumps(
                    {
                        "ok": True,
                        "message": (
                            f"Stored OAuth credentials for {name!r} "
                            "were removed."
                        ),
                        "servers": [
                            _status_json(server)
                            for server in status.servers
                        ],
                    },
                    ensure_ascii=False,
                ),
                details={
                    "kind": "io",
                    "input": name,
                    "output": _status_summary(status.servers),
                },
            )
        except Exception as exc:
            return _error_execution("mcp", str(exc))

    names = {
        "mcp_list_servers",
        "mcp_reload",
        "mcp_test_server",
        "mcp_add_server",
        "mcp_remove_server",
        "mcp_set_enabled",
        "mcp_authorize_server",
        "mcp_revoke_server_auth",
    }
    reserved_names.update(names)
    invocation = manager.create_tools(set(reserved_names))
    management = [
        AgentTool(
            definition=Tool(
                name="mcp_list_servers",
                description=(
                    "List configured MCP servers, connection state, cached "
                    "tool counts, and config diagnostics."
                ),
                parameters=_object_schema(),
            ),
            handler=mcp_list_servers,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_reload",
                description=(
                    "Clear cached MCP tool manifests and rediscover configured "
                    "servers. New or removed tools apply on the next turn."
                ),
                parameters=_object_schema(),
            ),
            handler=mcp_reload,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_test_server",
                description="Connect to one configured MCP server and list its tools.",
                parameters=_object_schema(
                    {"name": {"type": "string"}},
                    required=["name"],
                ),
            ),
            handler=mcp_test_server,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_add_server",
                description=(
                    "Add or replace one MCP server in the global Ling "
                    "MCP config. "
                    "Provide exactly one of url (HTTP) or command (stdio). "
                    "For an HTTP OAuth server set auth=oauth, then call "
                    "mcp_authorize_server after saving. "
                    "This changes the tools available to future turns and "
                    "always requires approval."
                ),
                parameters=_object_schema(
                    {
                        "name": {"type": "string"},
                        "url": {"type": "string"},
                        "headers": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "command": {"type": "string"},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "env": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "cwd": {"type": "string"},
                        "enabled": {"type": "boolean", "default": True},
                        "auth": {
                            "type": "string",
                            "enum": ["none", "oauth"],
                            "default": "none",
                        },
                        "scope": {"type": "string"},
                        "trust_annotations": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    required=["name"],
                ),
            ),
            handler=mcp_add_server,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_remove_server",
                description=(
                    "Remove one MCP server. Requires confirm=true and approval."
                ),
                parameters=_object_schema(
                    {
                        "name": {"type": "string"},
                        "confirm": {"type": "boolean", "const": True},
                    },
                    required=["name", "confirm"],
                ),
            ),
            handler=mcp_remove_server,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_set_enabled",
                description=(
                    "Enable or disable a configured MCP server. Takes effect "
                    "on the next turn and requires approval."
                ),
                parameters=_object_schema(
                    {
                        "name": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    required=["name", "enabled"],
                ),
            ),
            handler=mcp_set_enabled,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_authorize_server",
                description=(
                    "Start OAuth authorization for one HTTP MCP server. "
                    "Opens the browser, completes PKCE with a localhost "
                    "callback, stores tokens securely, and reconnects. "
                    "Discovered tools become available on the next turn."
                ),
                parameters=_object_schema(
                    {"name": {"type": "string"}},
                    required=["name"],
                ),
            ),
            handler=mcp_authorize_server,
        ),
        AgentTool(
            definition=Tool(
                name="mcp_revoke_server_auth",
                description=(
                    "Remove stored OAuth credentials for one MCP server and "
                    "disconnect it. Requires approval."
                ),
                parameters=_object_schema(
                    {"name": {"type": "string"}},
                    required=["name"],
                ),
            ),
            handler=mcp_revoke_server_auth,
        ),
    ]
    invocation_tools = await invocation
    return [*management, *invocation_tools]


def _server_from_call(call: ToolCall) -> McpServerConfig:
    name = _string_arg(call, "name")
    if len(name) > 64:
        raise ValueError("name must be at most 64 characters")
    url = call.arguments.get("url")
    command = call.arguments.get("command")
    if isinstance(url, str) and url.strip() and command is None:
        parsed_url = urlparse(url.strip())
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        headers = call.arguments.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in headers.items()
        ):
            raise ValueError(
                "headers must be an object of string values"
            )
        auth = _auth(call)
        if auth == "oauth" and any(
            key.lower() == "authorization" for key in headers
        ):
            raise ValueError(
                "OAuth cannot be combined with a static Authorization header"
            )
        return McpServerConfig(
            name=name,
            kind="http",
            enabled=_bool(call, "enabled", True),
            url=url.strip(),
            headers=dict(headers),
            auth=auth,
            oauth_scope=_optional_string(call, "scope"),
            trust_annotations=_bool(
                call,
                "trust_annotations",
                False,
            ),
        )
    if isinstance(command, str) and command.strip() and url is None:
        args = call.arguments.get("args", [])
        env = call.arguments.get("env", {})
        cwd = call.arguments.get("cwd")
        if not isinstance(args, list) or not all(
            isinstance(value, str) for value in args
        ):
            raise ValueError("args must be an array of strings")
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        ):
            raise ValueError("env must be an object of string values")
        if cwd is not None and not isinstance(cwd, str):
            raise ValueError("cwd must be a string")
        return McpServerConfig(
            name=name,
            kind="stdio",
            enabled=_bool(call, "enabled", True),
            command=command.strip(),
            args=tuple(args),
            env=dict(env),
            cwd=cwd,
            trust_annotations=_bool(
                call,
                "trust_annotations",
                False,
            ),
        )
    raise ValueError("provide exactly one of url or command")


def _flatten_call_result(result: types.CallToolResult) -> str:
    parts: list[str] = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            parts.append(content.text)
        elif isinstance(content, types.ImageContent):
            parts.append(
                f"[image omitted: {content.mimeType}, "
                f"{len(content.data)} base64 chars]"
            )
        elif isinstance(content, types.AudioContent):
            parts.append(
                f"[audio omitted: {content.mimeType}, "
                f"{len(content.data)} base64 chars]"
            )
        else:
            dumped = content.model_dump(
                by_alias=True,
                mode="json",
                exclude_none=True,
            )
            parts.append(json.dumps(dumped, ensure_ascii=False))
    if not parts and result.structuredContent is not None:
        parts.append(
            json.dumps(
                result.structuredContent,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    output = "\n".join(parts).strip()
    return output or "(MCP tool returned no output)"


def _status_json(status: McpStatus) -> dict[str, Any]:
    return {
        "name": status.name,
        "kind": status.kind,
        "target": status.target,
        "enabled": status.enabled,
        "state": status.state,
        "auth": status.auth,
        **(
            {"authorized": status.authorized}
            if status.authorized is not None
            else {}
        ),
        "toolCount": status.tool_count,
        "tools": list(status.tools),
        **({"error": status.error} if status.error else {}),
    }


def _status_summary(statuses: tuple[McpStatus, ...]) -> str:
    lines = [
        f"{server.name}: {server.state}, {server.tool_count} tool(s)"
        for server in statuses
    ]
    return "\n".join(lines) or "No MCP servers configured"


def _preview(value: str) -> str:
    return value if len(value) <= 1_000 else value[:1_000] + "\n[truncated]"


def _error_execution(server: str, message: str) -> ToolExecution:
    return ToolExecution(
        output=json.dumps(
            {"ok": False, "server": server, "message": message},
            ensure_ascii=False,
        ),
        is_error=True,
        details={
            "kind": "io",
            "input": server,
            "error": message,
        },
    )


def _string_arg(call: ToolCall, name: str) -> str:
    value = call.arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _bool(call: ToolCall, name: str, default: bool) -> bool:
    value = call.arguments.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _auth(call: ToolCall) -> str:
    value = call.arguments.get("auth", "none")
    if value not in ("none", "oauth"):
        raise ValueError("auth must be 'none' or 'oauth'")
    return value


def _optional_string(call: ToolCall, name: str) -> str | None:
    value = call.arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value.strip() or None


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        **({"required": required} if required else {}),
        "additionalProperties": False,
    }


__all__ = ["create_mcp_agent_tools", "create_mcp_tools"]

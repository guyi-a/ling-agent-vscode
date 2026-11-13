"""MCP client lifecycle, manifests, and lazy pooled connections."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from ..effects import McpEffect, is_private_web_target
from ..paths import default_mcp_state_root
from .auth import (
    McpCredentialError,
    McpTokenStore,
    OAuthLoopback,
    create_oauth_provider,
)
from .config import (
    McpConfig,
    McpConfigIssue,
    McpConfigStore,
    McpServerConfig,
    load_mcp_config,
)

_MAX_TOOL_NAME_LENGTH = 64
_DEFAULT_IDLE_TTL_SECONDS = 120.0
_MAX_CACHE_CHARS = 1_000_000


class McpConnectionError(RuntimeError):
    pass


class McpCancelled(RuntimeError):
    pass


class McpNeedsAuthorization(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class McpToolDescriptor:
    remote_name: str
    public_name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class McpStatus:
    name: str
    kind: str
    target: str
    enabled: bool
    state: str
    auth: str = "none"
    authorized: bool | None = None
    tool_count: int = 0
    tools: tuple[str, ...] = ()
    error: str | None = None


@dataclass(slots=True, frozen=True)
class McpRuntimeStatus:
    config_path: str
    servers: tuple[McpStatus, ...]
    issues: tuple[McpConfigIssue, ...]


@dataclass(slots=True)
class _CallCommand:
    remote_name: str
    arguments: dict[str, Any]
    timeout_seconds: float
    cancel_event: asyncio.Event | None
    future: asyncio.Future[types.CallToolResult]


class _McpConnection:
    """One live MCP session owned by a dedicated asyncio task."""

    def __init__(
        self,
        server: McpServerConfig,
        *,
        on_tools_changed: Any | None = None,
        oauth_provider: Any | None = None,
    ) -> None:
        self.server = server
        self.state = "connecting"
        self.error: str | None = None
        self._on_tools_changed = on_tools_changed
        self._oauth_provider = oauth_provider
        self._stderr_file = tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            errors="replace",
        )
        self._ready: asyncio.Future[tuple[McpToolDescriptor, ...]] = (
            asyncio.get_running_loop().create_future()
        )
        self._queue: asyncio.Queue[_CallCommand | None] = asyncio.Queue()
        self._task = asyncio.create_task(
            self._run(),
            name=f"ling-mcp:{server.name}",
        )

    @property
    def alive(self) -> bool:
        return not self._task.done() and self.state == "connected"

    async def start(self) -> tuple[McpToolDescriptor, ...]:
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._ready),
                timeout=self.server.init_timeout_seconds,
            )
        except BaseException:
            await self.close()
            raise

    async def call_tool(
        self,
        remote_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
        cancel_event: asyncio.Event | None,
    ) -> types.CallToolResult:
        if not self.alive:
            raise McpConnectionError(
                self.error or f"MCP server {self.server.name!r} is unavailable."
            )
        future: asyncio.Future[types.CallToolResult] = (
            asyncio.get_running_loop().create_future()
        )
        await self._queue.put(
            _CallCommand(
                remote_name=remote_name,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
                future=future,
            )
        )
        return await future

    async def close(self) -> None:
        if self._task.done():
            return
        await self._queue.put(None)
        try:
            await asyncio.wait_for(
                asyncio.shield(self._task),
                timeout=5.0,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        stack = AsyncExitStack()
        try:
            session, tools = await self._open_session(stack)
            self.state = "connected"
            if not self._ready.done():
                self._ready.set_result(tools)
            while True:
                command = await self._queue.get()
                if command is None:
                    break
                await self._run_call(session, command)
        except BaseException as exc:
            self.state = "error"
            self.error = _format_mcp_error(
                exc,
                _read_stderr_tail(self._stderr_file),
            )
            if not self._ready.done():
                self._ready.set_exception(exc)
            while not self._queue.empty():
                pending = self._queue.get_nowait()
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(exc)
        finally:
            self.state = "closed"
            await stack.aclose()
            self._stderr_file.close()

    async def _open_session(
        self,
        stack: AsyncExitStack,
    ) -> tuple[ClientSession, tuple[McpToolDescriptor, ...]]:
        if self.server.kind == "stdio":
            params = StdioServerParameters(
                command=self.server.command or "",
                args=list(self.server.args),
                env={**os.environ, **self.server.env},
                cwd=self.server.cwd,
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(params, errlog=self._stderr_file)
            )
            return await self._initialize_session(
                stack,
                read_stream,
                write_stream,
            )

        headers = dict(self.server.headers)
        errors: list[Exception] = []
        for transport_name in ("streamable-http", "sse"):
            attempt = AsyncExitStack()
            try:
                if transport_name == "streamable-http":
                    http_client = httpx.AsyncClient(
                        headers=headers or None,
                        auth=self._oauth_provider,
                        timeout=httpx.Timeout(
                            connect=self.server.init_timeout_seconds,
                            read=max(
                                300.0,
                                self.server.call_timeout_seconds,
                            ),
                            write=self.server.call_timeout_seconds,
                            pool=self.server.init_timeout_seconds,
                        ),
                    )
                    read_stream, write_stream, _ = (
                        await attempt.enter_async_context(
                            streamable_http_client(
                                self.server.url or "",
                                http_client=http_client,
                            )
                        )
                    )
                else:
                    read_stream, write_stream = (
                        await attempt.enter_async_context(
                            sse_client(
                                self.server.url or "",
                                headers=headers or None,
                                timeout=self.server.init_timeout_seconds,
                                sse_read_timeout=max(
                                    300.0,
                                    self.server.call_timeout_seconds,
                                ),
                                auth=self._oauth_provider,
                            )
                        )
                    )
                result = await self._initialize_session(
                    attempt,
                    read_stream,
                    write_stream,
                )
                # Transfer ownership of both nested contexts to the long-lived
                # connection stack.
                stack.push_async_callback(attempt.aclose)
                return result
            except Exception as exc:
                errors.append(exc)
                await attempt.aclose()
        if errors:
            raise errors[0]
        raise McpConnectionError("No HTTP transport was attempted")

    async def _initialize_session(
        self,
        stack: AsyncExitStack,
        read_stream: Any,
        write_stream: Any,
    ) -> tuple[ClientSession, tuple[McpToolDescriptor, ...]]:
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(
                    seconds=self.server.call_timeout_seconds
                ),
                client_info=types.Implementation(
                    name="ling",
                    version="0.1.0",
                ),
                message_handler=self._handle_message,
            )
        )
        await session.initialize()
        tools: list[types.Tool] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                break
        return (
            session,
            tuple(_tool_descriptor(tool) for tool in tools),
        )

    async def _handle_message(self, message: Any) -> None:
        if (
            isinstance(message, types.ServerNotification)
            and getattr(message.root, "method", None)
            == "notifications/tools/list_changed"
        ):
            callback = self._on_tools_changed
            if callable(callback):
                callback()

    async def _run_call(
        self,
        session: ClientSession,
        command: _CallCommand,
    ) -> None:
        if command.future.done():
            return
        call_task = asyncio.create_task(
            session.call_tool(
                command.remote_name,
                command.arguments,
                read_timeout_seconds=timedelta(
                    seconds=command.timeout_seconds
                ),
            )
        )
        cancel_task = (
            asyncio.create_task(command.cancel_event.wait())
            if command.cancel_event is not None
            else None
        )
        tasks: set[asyncio.Task[Any]] = {call_task}
        if cancel_task is not None:
            tasks.add(cancel_task)
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=command.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if call_task in done:
                try:
                    command.future.set_result(await call_task)
                except Exception as exc:
                    command.future.set_exception(exc)
            elif cancel_task is not None and cancel_task in done:
                call_task.cancel()
                command.future.set_exception(McpCancelled("MCP call cancelled"))
            else:
                call_task.cancel()
                command.future.set_exception(
                    TimeoutError(
                        f"MCP tool {command.remote_name!r} timed out"
                    )
                )
        finally:
            for task in (call_task, cancel_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (call_task, cancel_task) if task is not None),
                return_exceptions=True,
            )


class McpManager:
    """Own MCP config, tool manifests, and pooled server connections."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        idle_ttl_seconds: float = _DEFAULT_IDLE_TTL_SECONDS,
        config_path: str | Path | None = None,
        state_root: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.idle_ttl_seconds = idle_ttl_seconds
        self.state_root = (
            Path(state_root).expanduser().resolve()
            if state_root is not None
            else default_mcp_state_root().expanduser().resolve()
        )
        self.auth_root = self.state_root / "mcp-auth"
        self.store = McpConfigStore(
            self.workspace_root,
            path=config_path,
        )
        self.config: McpConfig = self.store.load()
        self.cache_path = self.state_root / "mcp-cache.json"
        self._cache = _load_cache(self.cache_path)
        self._connections: dict[str, _McpConnection] = {}
        self._connect_locks: dict[str, asyncio.Lock] = {}
        self._idle_timers: dict[str, asyncio.TimerHandle] = {}
        self._active_calls: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._needs_auth: set[str] = set()
        self._bindings: dict[str, tuple[McpServerConfig, McpToolDescriptor]] = {}
        self._closed = False
        self._lock = asyncio.Lock()

    def reload(self) -> McpConfig:
        self.config = self.store.load()
        active_keys = {
            server.pool_key()
            for server in self.config.servers
            if server.enabled
        }
        for key in tuple(self._connections):
            if key not in active_keys:
                self._close_connection_now(key)
        self._cleanup_orphaned_credentials()
        return self.config

    async def create_tools(
        self,
        reserved_names: set[str],
    ) -> list[Any]:
        """Discover manifests and return lazily connecting AgentTool objects."""

        from .tools import create_mcp_agent_tools

        if self._closed:
            return []
        self.reload()
        self._bindings = {}
        discovered: list[tuple[McpServerConfig, McpToolDescriptor]] = []
        for server in self.config.servers:
            if not server.enabled:
                continue
            if (
                server.kind == "http"
                and server.auth == "oauth"
            ):
                authorized, credential_error = await self._authorized(server)
                if credential_error is not None:
                    self._errors[server.name] = credential_error
                    self._needs_auth.discard(server.name)
                    continue
                if not authorized:
                    self._needs_auth.add(server.name)
                    continue
            cache_key = _cache_key(server)
            cached = self._cache.get(cache_key)
            if cached is None:
                try:
                    connection = await self._connection(server)
                    raw_tools = await connection.start()
                    cached = [
                        _descriptor_to_cache(tool)
                        for tool in raw_tools
                    ]
                    self._cache[cache_key] = cached
                    self._save_cache()
                    self._errors.pop(server.name, None)
                    self._schedule_idle(server)
                except Exception as exc:
                    self._errors[server.name] = _format_mcp_error(
                        exc,
                        "",
                    )
                    continue
            descriptors = [
                _descriptor_from_cache(item)
                for item in cached
                if isinstance(item, dict)
            ]
            discovered.extend(
                (server, descriptor)
                for descriptor in descriptors
            )

        claimed = set(reserved_names)
        bound: list[tuple[McpServerConfig, McpToolDescriptor]] = []
        for server, descriptor in discovered:
            public_name = unique_mcp_tool_name(
                server.name,
                descriptor.remote_name,
                claimed,
            )
            claimed.add(public_name)
            updated = McpToolDescriptor(
                remote_name=descriptor.remote_name,
                public_name=public_name,
                description=descriptor.description,
                input_schema=descriptor.input_schema,
                annotations=descriptor.annotations,
            )
            self._bindings[public_name] = (server, updated)
            bound.append((server, updated))
        return create_mcp_agent_tools(self, bound)

    def effect_for_tool(self, public_name: str) -> McpEffect | None:
        binding = self._bindings.get(public_name)
        if binding is None:
            return None
        server, descriptor = binding
        annotations = descriptor.annotations
        read_only = annotations.get("readOnlyHint") is True
        destructive = annotations.get("destructiveHint") is True
        open_world = annotations.get("openWorldHint") is True
        return McpEffect(
            server=server.name,
            tool=descriptor.remote_name,
            transport=server.kind,
            target=server.target,
            private_host=(
                is_private_web_target(server.url)
                if server.kind == "http" and server.url
                else True
            ),
            read_only=read_only,
            destructive=destructive,
            open_world=open_world,
            trusted_annotations=server.trust_annotations,
        )

    async def call_tool(
        self,
        public_name: str,
        arguments: dict[str, Any],
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> types.CallToolResult:
        binding = self._bindings.get(public_name)
        if binding is None:
            raise McpConnectionError(f"Unknown MCP tool: {public_name}")
        server, descriptor = binding
        return await self._call_server_tool(
            server,
            descriptor.remote_name,
            arguments,
            cancel_event=cancel_event,
            retry=True,
        )

    async def status(self) -> McpRuntimeStatus:
        self.reload()
        statuses: list[McpStatus] = []
        for server in self.config.servers:
            cache = self._cache.get(_cache_key(server), [])
            tools = [
                item.get("remoteName")
                for item in cache
                if isinstance(item, dict)
                and isinstance(item.get("remoteName"), str)
            ]
            connection = self._connections.get(server.pool_key())
            authorized: bool | None = None
            if server.kind == "http" and server.auth == "oauth":
                authorized, credential_error = await self._authorized(server)
                if credential_error is not None:
                    self._errors[server.name] = credential_error
                    self._needs_auth.discard(server.name)
            if not server.enabled:
                state = "disabled"
            elif (
                server.name in self._needs_auth
                or (
                    server.kind == "http"
                    and server.auth == "oauth"
                    and not authorized
                )
            ):
                state = "needs-auth"
            elif server.name in self._errors:
                state = "error"
            elif connection is not None and connection.alive:
                state = "connected"
            elif cache:
                state = "configured"
            else:
                state = "not-connected"
            statuses.append(
                McpStatus(
                    name=server.name,
                    kind=server.kind,
                    target=server.target,
                    enabled=server.enabled,
                    state=state,
                    auth=server.auth,
                    authorized=authorized,
                    tool_count=len(tools),
                    tools=tuple(tool for tool in tools if tool),
                    error=self._errors.get(server.name),
                )
            )
        return McpRuntimeStatus(
            config_path=str(self.config.path),
            servers=tuple(statuses),
            issues=self.config.issues,
        )

    async def add_server(self, server: McpServerConfig) -> McpRuntimeStatus:
        self.store.upsert(server)
        self.reload()
        self._invalidate_bindings(server.name)
        if server.kind == "http" and server.auth == "oauth":
            self._needs_auth.add(server.name)
        return await self.status()

    async def set_enabled(
        self,
        name: str,
        enabled: bool,
    ) -> McpRuntimeStatus:
        self.store.set_enabled(name, enabled)
        self.reload()
        self._invalidate_bindings(name)
        if not enabled:
            self._needs_auth.discard(name)
        return await self.status()

    async def remove_server(self, name: str) -> McpRuntimeStatus:
        self.reload()
        current = self.config.get(name)
        if current is not None:
            await self._credential_store(current).clear()
        self.store.remove(name)
        self.reload()
        self._invalidate_bindings(name)
        self._needs_auth.discard(name)
        self._errors.pop(name, None)
        self._cache.pop(
            _cache_key(current) if current is not None else "",
            None,
        )
        self._save_cache()
        return await self.status()

    async def authorize_server(self, name: str) -> McpStatus:
        self.reload()
        server = self.config.get(name)
        if server is None:
            raise ValueError(f"Unknown MCP server: {name}")
        if server.kind != "http" or server.auth != "oauth":
            raise ValueError(
                f"MCP server {name!r} is not configured for OAuth"
            )
        store = self._credential_store(server)
        await store.clear()
        await self._drop_connection(server)
        loopback = OAuthLoopback(
            timeout_seconds=max(
                server.init_timeout_seconds,
                60.0,
            )
        )
        await loopback.start()
        try:
            connection = await self._connection_with_oauth(
                server,
                interactive_oauth=loopback,
            )
            tools = await connection.start()
            self._cache[_cache_key(server)] = [
                _descriptor_to_cache(tool) for tool in tools
            ]
            self._save_cache()
            self._needs_auth.discard(name)
            self._errors.pop(name, None)
            self._schedule_idle(server)
            return McpStatus(
                name=server.name,
                kind=server.kind,
                target=server.target,
                enabled=server.enabled,
                state="connected",
                auth=server.auth,
                authorized=True,
                tool_count=len(tools),
                tools=tuple(tool.remote_name for tool in tools),
            )
        finally:
            await loopback.close()

    async def revoke_server_auth(self, name: str) -> McpRuntimeStatus:
        self.reload()
        server = self.config.get(name)
        if server is None:
            raise ValueError(f"Unknown MCP server: {name}")
        if server.kind != "http" or server.auth != "oauth":
            raise ValueError(
                f"MCP server {name!r} is not configured for OAuth"
            )
        await self._credential_store(server).clear()
        await self._drop_connection(server)
        self._cache.pop(_cache_key(server), None)
        self._bindings = {
            public_name: binding
            for public_name, binding in self._bindings.items()
            if binding[0].name != name
        }
        self._needs_auth.add(name)
        return await self.status()

    async def test_server(self, name: str) -> McpStatus:
        self.reload()
        server = self.config.get(name)
        if server is None:
            raise ValueError(f"Unknown MCP server: {name}")
        connection = await self._connection(server)
        tools = await connection.start()
        cache_key = _cache_key(server)
        self._cache[cache_key] = [
            _descriptor_to_cache(tool) for tool in tools
        ]
        self._save_cache()
        self._schedule_idle(server)
        return McpStatus(
            name=server.name,
            kind=server.kind,
            target=server.target,
            enabled=server.enabled,
            state="connected",
            tool_count=len(tools),
            tools=tuple(tool.remote_name for tool in tools),
        )

    def invalidate_manifests(self) -> None:
        self._cache.clear()
        self._bindings.clear()
        self._errors.clear()
        self._save_cache()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connections = list(self._connections.values())
        self._connections.clear()
        for key in tuple(self._idle_timers):
            self._cancel_idle(key)
        for timer in self._idle_timers.values():
            timer.cancel()
        self._idle_timers.clear()
        await asyncio.gather(
            *(connection.close() for connection in connections),
            return_exceptions=True,
        )

    async def _call_server_tool(
        self,
        server: McpServerConfig,
        remote_name: str,
        arguments: dict[str, Any],
        *,
        cancel_event: asyncio.Event | None,
        retry: bool,
    ) -> types.CallToolResult:
        connection = await self._connection(server)
        self._cancel_idle(server.pool_key())
        self._active_calls[server.pool_key()] = (
            self._active_calls.get(server.pool_key(), 0) + 1
        )
        try:
            return await connection.call_tool(
                remote_name,
                arguments,
                timeout_seconds=server.call_timeout_seconds,
                cancel_event=cancel_event,
            )
        except BaseException as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise McpCancelled("MCP call cancelled") from exc
            if isinstance(exc, (McpError, McpCancelled)):
                raise
            await self._drop_connection(server)
            if retry:
                return await self._call_server_tool(
                    server,
                    remote_name,
                    arguments,
                    cancel_event=cancel_event,
                    retry=False,
                )
            self._errors[server.name] = _format_mcp_error(exc, "")
            raise
        finally:
            key = server.pool_key()
            self._active_calls[key] = max(
                0,
                self._active_calls.get(key, 1) - 1,
            )
            if key not in self._connections:
                self._active_calls.pop(key, None)
            else:
                self._schedule_idle(server)

    async def _connection(self, server: McpServerConfig) -> _McpConnection:
        if server.kind == "http" and server.auth == "oauth":
            authorized, credential_error = await self._authorized(server)
            if credential_error is not None:
                raise McpCredentialError(credential_error)
            if not authorized:
                raise McpNeedsAuthorization(
                    f"MCP server {server.name!r} requires OAuth authorization"
                )
        return await self._connection_with_oauth(
            server,
            interactive_oauth=None,
        )

    async def _connection_with_oauth(
        self,
        server: McpServerConfig,
        *,
        interactive_oauth: OAuthLoopback | None,
    ) -> _McpConnection:
        key = server.pool_key()
        connection = self._connections.get(key)
        if connection is not None and connection.alive:
            self._cancel_idle(key)
            return connection
        lock = self._connect_locks.setdefault(key, asyncio.Lock())
        async with lock:
            connection = self._connections.get(key)
            if connection is not None and connection.alive:
                return connection
            connection = _McpConnection(
                server,
                on_tools_changed=lambda: self._manifest_changed(server),
                oauth_provider=self._oauth_provider(
                    server,
                    interactive_oauth,
                ),
            )
            self._connections[key] = connection
            try:
                await connection.start()
            except BaseException:
                self._connections.pop(key, None)
                raise
            self._errors.pop(server.name, None)
            return connection

    def _oauth_provider(
        self,
        server: McpServerConfig,
        loopback: OAuthLoopback | None,
    ) -> Any | None:
        if server.kind != "http" or server.auth != "oauth":
            return None
        return create_oauth_provider(
            server,
            self._credential_store(server),
            loopback,
        )

    def _credential_store(self, server: McpServerConfig) -> McpTokenStore:
        return McpTokenStore(self.auth_root, server)

    def _cleanup_orphaned_credentials(self) -> None:
        if self.config.issues:
            return
        active = {
            self._credential_store(server).path
            for server in self.config.servers
            if server.kind == "http" and server.auth == "oauth"
        }
        directory = self.auth_root
        if not directory.is_dir():
            return
        for path in directory.glob("*.bin"):
            if path in active:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    async def _authorized(
        self,
        server: McpServerConfig,
    ) -> tuple[bool, str | None]:
        try:
            return (
                await self._credential_store(server).authorized(),
                None,
            )
        except McpCredentialError as exc:
            return False, str(exc)

    async def _drop_connection(self, server: McpServerConfig) -> None:
        key = server.pool_key()
        connection = self._connections.pop(key, None)
        self._cancel_idle(key)
        self._active_calls.pop(key, None)
        if connection is not None:
            await connection.close()

    def _close_connection_now(self, key: str) -> None:
        self._cancel_idle(key)
        connection = self._connections.pop(key, None)
        if connection is None:
            return
        asyncio.create_task(connection.close())

    def _schedule_idle(self, server: McpServerConfig) -> None:
        key = server.pool_key()
        if self._active_calls.get(key, 0) > 0:
            return
        self._cancel_idle(key)

        def close_idle() -> None:
            if self._active_calls.get(key, 0) > 0:
                return
            self._close_connection_now(key)

        self._idle_timers[key] = asyncio.get_running_loop().call_later(
            self.idle_ttl_seconds,
            close_idle,
        )

    def _cancel_idle(self, key: str) -> None:
        timer = self._idle_timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(
                    {"servers": self._cache},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _invalidate_bindings(self, server_name: str) -> None:
        self._bindings = {
            public_name: binding
            for public_name, binding in self._bindings.items()
            if binding[0].name != server_name
        }

    def _manifest_changed(self, server: McpServerConfig) -> None:
        self._cache.pop(_cache_key(server), None)
        self._save_cache()


def unique_mcp_tool_name(
    server_name: str,
    remote_name: str,
    claimed: set[str],
) -> str:
    server_slug = _slug(server_name, "server")
    remote_slug = _slug(remote_name, "tool")
    prefix = f"mcp_{server_slug}__"
    room = _MAX_TOOL_NAME_LENGTH - len(prefix) - 9
    if len(remote_slug) > room:
        digest = hashlib.sha256(
            remote_name.encode("utf-8")
        ).hexdigest()[:8]
        remote_slug = f"{remote_slug[:room]}_{digest}"
    base = (prefix + remote_slug)[:_MAX_TOOL_NAME_LENGTH]
    candidate = base
    suffix_index = 2
    while candidate in claimed:
        suffix = f"_{suffix_index}"
        candidate = base[: _MAX_TOOL_NAME_LENGTH - len(suffix)] + suffix
        suffix_index += 1
    return candidate


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()
    if not slug:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        slug = f"{fallback}_{digest}"
    if not slug[0].isalpha():
        slug = f"{fallback}_{slug}"
    return slug[:24]


def _tool_descriptor(tool: types.Tool) -> McpToolDescriptor:
    annotations = (
        tool.annotations.model_dump(by_alias=True, exclude_none=True)
        if tool.annotations is not None
        else {}
    )
    schema = tool.inputSchema
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    description = (tool.description or "").strip()
    if not description:
        description = f"MCP tool {tool.name}"
    return McpToolDescriptor(
        remote_name=tool.name,
        public_name=tool.name,
        description=description,
        input_schema=schema,
        annotations=annotations,
    )


def _cache_key(server: McpServerConfig) -> str:
    return f"{server.name}:{server.pool_key()}"


def _descriptor_to_cache(
    tool: McpToolDescriptor,
) -> dict[str, Any]:
    return {
        "remoteName": tool.remote_name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": tool.annotations,
    }


def _descriptor_from_cache(data: dict[str, Any]) -> McpToolDescriptor:
    remote_name = data.get("remoteName")
    description = data.get("description")
    input_schema = data.get("inputSchema")
    annotations = data.get("annotations")
    if (
        not isinstance(remote_name, str)
        or not remote_name
        or not isinstance(description, str)
        or not isinstance(input_schema, dict)
    ):
        raise ValueError("Invalid cached MCP tool descriptor")
    return McpToolDescriptor(
        remote_name=remote_name,
        public_name=remote_name,
        description=description,
        input_schema=input_schema,
        annotations=(
            annotations if isinstance(annotations, dict) else {}
        ),
    )


def _load_cache(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        if path.stat().st_size > _MAX_CACHE_CHARS:
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    servers = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {
        key: value
        for key, value in servers.items()
        if isinstance(key, str)
        and isinstance(value, list)
        and all(isinstance(item, dict) for item in value)
    }


def _format_mcp_error(error: BaseException, stderr: str) -> str:
    message = str(error).strip() or type(error).__name__
    if stderr:
        message += f"; stderr: {stderr[-2_000:]}"
    return message


def _read_stderr_tail(
    handle: Any,
    limit: int = 8_192,
) -> str:
    try:
        handle.flush()
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read().strip()
    except (OSError, ValueError):
        return ""


__all__ = [
    "McpCancelled",
    "McpConnectionError",
    "McpManager",
    "McpRuntimeStatus",
    "McpStatus",
    "McpToolDescriptor",
    "unique_mcp_tool_name",
]

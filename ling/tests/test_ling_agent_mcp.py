"""Tests for MCP config parsing, naming, and stdio integration."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientMetadata, OAuthToken
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from ling_ai.types import ToolCall
from ling_agent.mcp import McpManager, load_mcp_config
from ling_agent.mcp.auth import (
    McpTokenStore,
    OAuthLoopback,
    create_oauth_provider,
)
from ling_agent.mcp.config import McpServerConfig
from ling_agent.mcp.manager import unique_mcp_tool_name
from ling_core import ToolContext


def _manager(root: Path, **kwargs: Any) -> McpManager:
    return McpManager(
        root,
        config_path=root / ".agents" / "mcp.json",
        state_root=root / ".agents",
        **kwargs,
    )


def _auth_root(root: Path) -> Path:
    return root / ".agents" / "mcp-auth"


def test_mcp_config_parses_stdio_and_http() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / ".agents" / "mcp.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "wiki": {
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "Bearer test"},
                        },
                        "local": {
                            "command": "python",
                            "args": ["server.py"],
                            "env": {"TOKEN": "redacted"},
                            "cwd": ".",
                        },
                        "broken": {"url": "ftp://bad"},
                    }
                }
            ),
            encoding="utf-8",
        )

        config = load_mcp_config(root, path=path)

        assert [server.name for server in config.servers] == [
            "local",
            "wiki",
        ]
        assert config.get("wiki") is not None
        assert config.get("wiki").kind == "http"  # type: ignore[union-attr]
        assert config.get("local") is not None
        assert config.get("local").kind == "stdio"  # type: ignore[union-attr]
        assert (  # type: ignore[union-attr]
            Path(config.get("local").cwd).resolve() == root.resolve()
        )
        assert len(config.issues) == 1
        assert config.issues[0].name == "broken"


def test_mcp_paths_are_user_level_and_ignore_project_config() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config_path = (root / "config" / "mcp.json").resolve()
        state_root = (root / "state").resolve()
        workspace = root / "workspace"
        project_config = workspace / ".agents" / "mcp.json"
        project_config.parent.mkdir(parents=True)
        project_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "project-only": {
                            "url": "https://example.com/mcp",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "LING_MCP_CONFIG": str(config_path),
                "LING_MCP_STATE_ROOT": str(state_root),
            },
        ):
            first = McpManager(workspace)
            second = McpManager(root / "other")
            try:
                assert first.config.path == config_path.resolve()
                assert first.config.servers == ()
                assert first.cache_path == state_root / "mcp-cache.json"
                assert first.cache_path == second.cache_path
                assert first.auth_root == state_root / "mcp-auth"
                assert first.auth_root == second.auth_root
            finally:
                asyncio.run(first.close())
                asyncio.run(second.close())


def test_mcp_tool_names_are_ascii_bounded_and_unique() -> None:
    claimed = {"read_file"}
    first = unique_mcp_tool_name(
        "中文服务",
        "search/repositories?with=a very long name",
        claimed,
    )
    claimed.add(first)
    second = unique_mcp_tool_name(
        "中文服务",
        "search/repositories?with=a very long name",
        claimed,
    )

    assert first.startswith("mcp_")
    assert first.isascii()
    assert len(first) <= 64
    assert second != first
    assert len(second) <= 64


async def _oauth_server_needs_authorization(root: Path) -> None:
    path = root / ".agents" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "oauth": {
                        "url": "https://example.com/mcp",
                        "auth": "oauth",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = _manager(root)
    try:
        from ling_agent.mcp.tools import create_mcp_tools

        tools = await create_mcp_tools(manager, {"read_file"})
        assert not any(
            tool.definition.name.endswith("__echo")
            for tool in tools
        )
        status = await manager.status()
        assert status.servers[0].state == "needs-auth"
        assert status.servers[0].auth == "oauth"
        assert status.servers[0].authorized is False
    finally:
        await manager.close()


def test_mcp_oauth_server_requires_explicit_authorization() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(
            _oauth_server_needs_authorization(Path(temp))
        )


async def _remove_oauth_server_clears_credentials(root: Path) -> None:
    path = root / ".agents" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "oauth": {
                        "url": "https://example.com/mcp",
                        "auth": "oauth",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = _manager(root)
    try:
        server = manager.config.get("oauth")
        assert server is not None
        store = McpTokenStore(_auth_root(root), server)
        await store.set_tokens(
            OAuthToken(
                access_token="old-access",
                refresh_token="old-refresh",
            )
        )
        assert await store.authorized()

        await manager.remove_server("oauth")
        assert not await store.authorized()

        await manager.add_server(server)
        status = await manager.status()
        assert status.servers[0].state == "needs-auth"
        assert status.servers[0].authorized is False

        await store.set_tokens(
            OAuthToken(access_token="another-access")
        )
        path.write_text(
            json.dumps({"mcpServers": {}}),
            encoding="utf-8",
        )
        manager.reload()
        assert not await store.authorized()
    finally:
        await manager.close()


def test_removing_mcp_server_clears_oauth_credentials() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(
            _remove_oauth_server_clears_credentials(Path(temp))
        )


async def _credential_store_round_trip(root: Path) -> None:
    server = McpServerConfig(
        name="oauth",
        kind="http",
        url="https://example.com/mcp",
        auth="oauth",
    )
    store = McpTokenStore(_auth_root(root), server)
    token = OAuthToken(
        access_token="secret-access-token",
        refresh_token="secret-refresh-token",
        expires_in=3600,
    )
    await store.set_tokens(token)
    assert await store.authorized()
    assert await store.get_tokens() == token
    assert b"secret-access-token" not in store.path.read_bytes()
    await store.clear()
    assert not await store.authorized()


def test_mcp_credentials_are_encrypted() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_credential_store_round_trip(Path(temp)))


async def _oauth_pkce_round_trip(root: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    challenge = {"value": "", "state": ""}
    exchanged = False

    async def mcp_endpoint(request: Request) -> JSONResponse:
        if request.headers.get("authorization") == "Bearer access-1":
            return JSONResponse({"ok": True})
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    f'Bearer resource_metadata="{base}/'
                    '.well-known/oauth-protected-resource"'
                )
            },
        )

    async def resource_metadata(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "resource": base,
                "authorization_servers": [base],
            }
        )

    async def auth_metadata(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register",
                "scopes_supported": ["read"],
                "response_types_supported": ["code"],
                "grant_types_supported": [
                    "authorization_code",
                    "refresh_token",
                ],
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256"],
            }
        )

    async def register(request: Request) -> JSONResponse:
        payload = await request.json()
        return JSONResponse(
            {
                "client_id": "ling-test-client",
                "redirect_uris": payload.get("redirect_uris", []),
                "token_endpoint_auth_method": "none",
                "grant_types": [
                    "authorization_code",
                    "refresh_token",
                ],
                "response_types": ["code"],
            },
            status_code=201,
        )

    async def token(request: Request) -> JSONResponse:
        nonlocal exchanged
        form = await request.form()
        if form.get("grant_type") != "authorization_code":
            return JSONResponse({"error": "unsupported"}, status_code=400)
        verifier = str(form.get("code_verifier") or "")
        import base64
        import hashlib

        digest = hashlib.sha256(verifier.encode()).digest()
        calculated = (
            base64.urlsafe_b64encode(digest)
            .decode()
            .rstrip("=")
        )
        if form.get("code") != "code-1" or calculated != challenge["value"]:
            return JSONResponse(
                {"error": "invalid_grant"},
                status_code=400,
            )
        exchanged = True
        return JSONResponse(
            {
                "access_token": "access-1",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "refresh-1",
            }
        )

    app = Starlette(
        routes=[
            Route("/mcp", mcp_endpoint),
            Route(
                "/.well-known/oauth-protected-resource",
                resource_metadata,
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                resource_metadata,
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                auth_metadata,
            ),
            Route(
                "/.well-known/openid-configuration",
                auth_metadata,
            ),
            Route("/register", register, methods=["POST"]),
            Route("/token", token, methods=["POST"]),
        ]
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    server_task = asyncio.create_task(server.serve())
    loopback = OAuthLoopback(timeout_seconds=10)
    await loopback.start()
    store = McpTokenStore(
        _auth_root(root),
        McpServerConfig(
            name="oauth",
            kind="http",
            url=f"{base}/mcp",
            auth="oauth",
            oauth_scope="read",
        ),
    )
    provider = create_oauth_provider(
        McpServerConfig(
            name="oauth",
            kind="http",
            url=f"{base}/mcp",
            auth="oauth",
            oauth_scope="read",
        ),
        store,
        loopback,
    )

    async def approve() -> None:
        for _ in range(100):
            if loopback.authorization_url:
                break
            await asyncio.sleep(0.02)
        assert loopback.authorization_url
        parsed = urlparse(loopback.authorization_url)
        query = parse_qs(parsed.query)
        challenge["value"] = query["code_challenge"][0]
        challenge["state"] = query["state"][0]
        assert query["code_challenge_method"][0] == "S256"
        callback = (
            f"{loopback.redirect_uri}?code=code-1"
            f"&state={challenge['state']}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.get(callback)
            assert response.status_code == 200

    try:
        await asyncio.sleep(0.1)
        approver = asyncio.create_task(approve())
        async with httpx.AsyncClient(auth=provider) as client:
            response = await client.get(f"{base}/mcp")
        await approver
        assert response.status_code == 200
        assert exchanged
        assert await store.authorized()
    finally:
        await loopback.close()
        server.should_exit = True
        await asyncio.gather(server_task, return_exceptions=True)


def test_mcp_oauth_pkce_round_trip() -> None:
    with (
        tempfile.TemporaryDirectory() as temp,
        # The flow normally opens the system browser; keep tests headless.
        patch("ling_agent.mcp.auth.webbrowser.open", return_value=True),
    ):
        asyncio.run(_oauth_pkce_round_trip(Path(temp)))


async def _stdio_round_trip(root: Path) -> None:
    path = root / ".agents" / "mcp.json"
    path.parent.mkdir(parents=True)
    fixture = Path(__file__).with_name("mcp_server_fixture.py")
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture": {
                        "command": sys.executable,
                        "args": [str(fixture)],
                        "cwd": str(root),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = _manager(root, idle_ttl_seconds=0.2)
    try:
        from ling_agent.mcp.tools import create_mcp_tools

        tools = await create_mcp_tools(manager, {"read_file"})
        by_name = {tool.definition.name: tool for tool in tools}
        echo_name = next(
            (
                name
                for name in by_name
                if name.endswith("__echo")
            ),
            "",
        )
        assert echo_name, (list(by_name), await manager.status())
        result = await by_name[echo_name].handler(
            ToolCall(
                id="call-echo",
                name=echo_name,
                arguments={"text": "hello"},
            ),
            ToolContext(step=1),
        )
        assert not result.is_error
        assert result.output == "echo:hello"
        effect = manager.effect_for_tool(echo_name)
        assert effect is not None
        assert effect.read_only is True
        assert effect.transport == "stdio"
    finally:
        await manager.close()


def test_mcp_stdio_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_stdio_round_trip(Path(temp)))


async def _streamable_http_round_trip(root: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = FastMCP("Ling MCP HTTP fixture", port=port)

    @server.tool(description="Echo text back to the caller.")
    def echo(text: str) -> str:
        return f"echo:{text}"

    server_task = asyncio.create_task(
        server.run_streamable_http_async()
    )
    path = root / ".agents" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "http-fixture": {
                        "url": f"http://127.0.0.1:{port}/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = _manager(root, idle_ttl_seconds=0.2)
    try:
        connected = False
        for _ in range(50):
            try:
                with socket.create_connection(
                    ("127.0.0.1", port),
                    timeout=0.2,
                ):
                    connected = True
                    break
            except OSError:
                await asyncio.sleep(0.1)
        assert connected
        await asyncio.sleep(0.5)
        from ling_agent.mcp.tools import create_mcp_tools

        by_name: dict[str, Any] = {}
        echo_name = ""
        for _ in range(5):
            tools = await create_mcp_tools(manager, {"read_file"})
            by_name = {
                tool.definition.name: tool
                for tool in tools
            }
            echo_name = next(
                (
                    name
                    for name in by_name
                    if name.endswith("__echo")
                ),
                "",
            )
            if echo_name:
                break
            manager.invalidate_manifests()
            await asyncio.sleep(0.2)
        assert echo_name, (list(by_name), await manager.status())
        result = await by_name[echo_name].handler(
            ToolCall(
                id="call-http",
                name=echo_name,
                arguments={"text": "remote"},
            ),
            ToolContext(step=1),
        )
        assert not result.is_error
        assert result.output == "echo:remote"
    finally:
        await manager.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)


def test_mcp_streamable_http_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_streamable_http_round_trip(Path(temp)))


if __name__ == "__main__":
    test_mcp_config_parses_stdio_and_http()
    test_mcp_tool_names_are_ascii_bounded_and_unique()
    test_mcp_credentials_are_encrypted()
    test_mcp_oauth_server_requires_explicit_authorization()
    test_removing_mcp_server_clears_oauth_credentials()
    test_mcp_oauth_pkce_round_trip()
    test_mcp_stdio_round_trip()
    test_mcp_streamable_http_round_trip()

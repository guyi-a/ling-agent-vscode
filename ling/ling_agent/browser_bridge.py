"""Server side of the Kro browser-extension bridge.

The extension talks to us over a local HTTP/WebSocket endpoint. This
module owns discovery, the page registry, and command/result futures.
It is intentionally independent of MCP and of the coding-agent loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

PING_CLIENT_ID = "kro-browser-bridge"
PING_SALT = "kro-2026"
PING_SIG_HEADER = "X-Kro-Client-Sig"
PING_PATH = "/chrome-bridge/ping"
WS_PATH = "/chrome-bridge/ws"
SERVER_NAME = "krow-browser-bridge"
PROTOCOL_VERSION = "1.0"
WS_CLOSE_AUTH_FAILED = 4401
DEFAULT_PORT = 17323
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
DEFAULT_CONNECT_WAIT_SECONDS = 3.0
MAX_RESULT_BYTES = 32 * 1024

PING_SIGNATURE = hashlib.sha256(
    f"{PING_CLIENT_ID}:{PING_SALT}".encode("utf-8")
).hexdigest()

READONLY_ACTIONS = frozenset(
    {
        "extension_status",
        "list_sessions",
        "list_pages",
        "read_state",
        "extract",
        "describe_element",
        "wait_for",
        "scroll",
    }
)
SCRIPT_ACTIONS = frozenset({"execute_script"})
MUTATING_ACTIONS = frozenset(
    {
        "open_tab",
        "focus_page",
        "close_tab",
        "click",
        "hover",
        "dblclick",
        "rightclick",
        "type",
        "press",
        "go_back",
        "reload",
    }
)


class BrowserBridgeError(RuntimeError):
    """A bridge failure with a stable, UI-friendly code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now_ms() -> int:
    return int(time.time() * 1000)


def _frame_payload(frame: dict[str, Any]) -> dict[str, Any]:
    payload = frame.get("payload")
    return payload if isinstance(payload, dict) else {}


def _normalize_frame_type(frame: dict[str, Any]) -> str:
    kind = frame.get("type")
    if kind == "event":
        name = _frame_payload(frame).get("name")
        if name == "page_updated":
            return "page_updated"
        if name in ("page_closed", "page_removed"):
            return "page_removed"
    return kind if isinstance(kind, str) else ""


def _allowed_origin(origin: str) -> bool:
    return (
        origin.startswith("chrome-extension://")
        or origin.startswith("edge-extension://")
        or origin in ("http://localhost:5173", "http://127.0.0.1:5173")
    )


@dataclass(slots=True)
class BrowserClient:
    session_id: str
    browser_id: str
    label: str = "Chrome Extension"
    extension_version: str = ""
    connected_at: int = 0
    last_seen_at: int = 0
    hello_received: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "browserId": self.browser_id,
            "label": self.label,
            "lastSeenAt": self.last_seen_at,
            "extensionVersion": self.extension_version,
        }


@dataclass(slots=True)
class BrowserPage:
    page_id: str
    browser_id: str
    window_id: int
    tab_id: int
    url: str
    title: str
    active: bool
    controllable: bool = True
    context_role: str = ""
    last_seen_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageId": self.page_id,
            "browserId": self.browser_id,
            "windowId": self.window_id,
            "tabId": self.tab_id,
            "url": self.url,
            "title": self.title,
            "active": self.active,
            "controllable": self.controllable,
            "contextRole": self.context_role,
            "lastSeenAt": self.last_seen_at,
        }


class BrowserRegistry:
    """Track extension sessions and the tabs they report."""

    def __init__(self) -> None:
        self.instance_id = str(uuid.uuid4())
        self.discovery_token = str(uuid.uuid4())
        self._clients: dict[str, BrowserClient] = {}
        self._sessions_by_browser: dict[str, str] = {}
        self._pages: dict[str, BrowserPage] = {}
        self._page_keys: dict[tuple[str, int], str] = {}

    def ping_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "server": SERVER_NAME,
            "instanceId": self.instance_id,
            "protocolVersion": PROTOCOL_VERSION,
            "wsPath": WS_PATH,
            "token": self.discovery_token,
        }

    def register_client(self) -> BrowserClient:
        session_id = str(uuid.uuid4())
        client = BrowserClient(
            session_id=session_id,
            browser_id=str(uuid.uuid4()),
            connected_at=_now_ms(),
            last_seen_at=_now_ms(),
        )
        self._clients[session_id] = client
        return client

    def set_browser_id(
        self,
        session_id: str,
        browser_id: str,
        label: str = "",
        extension_version: str = "",
    ) -> BrowserClient | None:
        client = self._clients.get(session_id)
        if client is None:
            return None
        old = self._sessions_by_browser.pop(client.browser_id, None)
        if old is not None and old != session_id:
            # A reconnect can reuse the same stable browser id. Keep the
            # old connection alive but stop listing it as a live session.
            old_client = self._clients.get(old)
            if old_client is not None:
                old_client.hello_received = False
        client.browser_id = browser_id or client.browser_id
        if label:
            client.label = label
        if extension_version:
            client.extension_version = extension_version
        client.last_seen_at = _now_ms()
        client.hello_received = True
        self._sessions_by_browser[client.browser_id] = session_id
        return client

    def unregister_client(self, session_id: str) -> BrowserClient | None:
        client = self._clients.pop(session_id, None)
        if client is None:
            return None
        if self._sessions_by_browser.get(client.browser_id) == session_id:
            self._sessions_by_browser.pop(client.browser_id, None)
        still_owned = any(
            other.browser_id == client.browser_id
            for other in self._clients.values()
        )
        if not still_owned:
            for page_id, page in list(self._pages.items()):
                if page.browser_id == client.browser_id:
                    self.remove_page(client.browser_id, page.tab_id)
        return client

    def client_by_browser(self, browser_id: str) -> BrowserClient | None:
        session_id = self._sessions_by_browser.get(browser_id)
        if session_id is None:
            return None
        return self._clients.get(session_id)

    def get_client(self, session_id: str) -> BrowserClient | None:
        return self._clients.get(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            client.to_dict()
            for client in self._clients.values()
            if client.hello_received
        ]

    def extension_versions(self) -> list[str]:
        versions: list[str] = []
        for client in self._clients.values():
            if client.hello_received and client.extension_version:
                versions.append(client.extension_version)
        return versions

    def upsert_page(
        self,
        browser_id: str,
        window_id: int,
        tab_id: int,
        url: str,
        title: str,
        active: bool,
        context_role: str = "",
    ) -> BrowserPage:
        key = (browser_id, tab_id)
        page_id = self._page_keys.get(key)
        page = self._pages.get(page_id) if page_id else None
        if page is None:
            page = BrowserPage(
                page_id=f"page_{uuid.uuid4().hex[:8]}",
                browser_id=browser_id,
                window_id=window_id,
                tab_id=tab_id,
                url=url,
                title=title,
                active=active,
                context_role=context_role,
                last_seen_at=_now_ms(),
            )
            self._pages[page.page_id] = page
            self._page_keys[key] = page.page_id
            return page
        page.window_id = window_id
        page.url = url
        page.title = title
        page.active = active
        page.context_role = context_role
        page.last_seen_at = _now_ms()
        return page

    def remove_page(self, browser_id: str, tab_id: int) -> None:
        page_id = self._page_keys.pop((browser_id, tab_id), None)
        if page_id is not None:
            self._pages.pop(page_id, None)

    def get_page(self, browser_id: str, page_id: str) -> BrowserPage | None:
        page = self._pages.get(page_id)
        if page is None or page.browser_id != browser_id:
            return None
        return page

    def list_pages(self, browser_id: str) -> list[dict[str, Any]]:
        return [
            page.to_dict()
            for page in self._pages.values()
            if page.browser_id == browser_id
        ]

    def clear(self) -> None:
        self._clients.clear()
        self._sessions_by_browser.clear()
        self._pages.clear()
        self._page_keys.clear()


class BrowserBridge:
    """Own the extension-facing server and command futures."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.command_timeout = command_timeout
        self.registry = BrowserRegistry()
        self._server: Any | None = None
        self._start_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._connections: dict[str, Any] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, tuple[str, asyncio.Future[Any]]] = {}
        self._ready = asyncio.Event()

    @property
    def started(self) -> bool:
        return self._server is not None

    @property
    def bound_port(self) -> int:
        if self._server is None:
            return self.port
        sockets = getattr(self._server, "sockets", None) or []
        if not sockets:
            return self.port
        return int(sockets[0].getsockname()[1])

    async def ensure_started(self) -> None:
        if self._server is not None:
            return
        async with self._start_lock:
            if self._server is not None:
                return
            try:
                from websockets.asyncio.server import serve
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise BrowserBridgeError(
                    "DEPENDENCY_MISSING",
                    "The websockets package is missing. "
                    "Run `pip install -r requirements.txt` and rebuild "
                    "the sidecar.",
                ) from exc
            try:
                self._server = await serve(
                    self._handle_connection,
                    self.host,
                    self.port,
                    process_request=self._process_request,
                    compression=None,
                    max_size=4 * 1024 * 1024,
                )
            except OSError as exc:
                self._server = None
                if getattr(exc, "errno", None) in (48, 98, 10048):
                    raise BrowserBridgeError(
                        "PORT_IN_USE",
                        f"Browser bridge port {self.port} is already in use. "
                        "Claude Code's kro-bridge MCP server (or another "
                        "Ling window) may own it. Stop that process and "
                        "retry.",
                    ) from exc
                raise BrowserBridgeError(
                    "PORT_IN_USE",
                    f"Unable to listen on {self.host}:{self.port}: {exc}",
                ) from exc
            if self.port == 0:
                self.port = self.bound_port

    async def stop(self) -> None:
        async with self._stop_lock:
            server = self._server
            if server is None:
                return
            self._server = None
            for _, future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(
                        BrowserBridgeError(
                            "SHUTDOWN",
                            "The browser bridge is shutting down.",
                        )
                    )
            self._pending.clear()
            for connection in list(self._connections.values()):
                try:
                    await connection.close(1001, "server shutting down")
                except Exception:
                    pass
            self._connections.clear()
            self._send_locks.clear()
            self.registry.clear()
            self._ready.clear()
            server.close()
            await server.wait_closed()

    async def _process_request(self, connection: Any, request: Any) -> Any:
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        path = request.path.split("?", 1)[0]
        origin = request.headers.get("Origin", "")
        cors_headers: list[tuple[str, str]] = []
        if origin and _allowed_origin(origin):
            cors_headers = [
                ("Access-Control-Allow-Origin", origin),
                (
                    "Access-Control-Allow-Methods",
                    "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                ),
                (
                    "Access-Control-Allow-Headers",
                    "Content-Type, X-Kro-Client-Sig, X-Bridge-Token",
                ),
                ("Access-Control-Max-Age", "600"),
            ]

        if path == PING_PATH:
            if request.method == "OPTIONS":
                return Response(
                    204,
                    "No Content",
                    Headers(cors_headers + [("Content-Length", "0")]),
                    b"",
                )
            if request.method == "GET":
                signature = request.headers.get(PING_SIG_HEADER, "")
                if not hmac.compare_digest(signature, PING_SIGNATURE):
                    body = json.dumps(
                        {"error": "invalid client signature"}
                    ).encode("utf-8")
                    return Response(
                        403,
                        "Forbidden",
                        Headers(
                            cors_headers
                            + [
                                ("Content-Type", "application/json"),
                                ("Content-Length", str(len(body))),
                            ]
                        ),
                        body,
                    )
            if request.method not in ("GET", "HEAD"):
                return Response(
                    405,
                    "Method Not Allowed",
                    Headers(cors_headers + [("Content-Length", "0")]),
                    b"",
                )
            body = json.dumps(self.registry.ping_payload()).encode("utf-8")
            return Response(
                200,
                "OK",
                Headers(
                    cors_headers
                    + [
                        ("Content-Type", "application/json; charset=utf-8"),
                        ("Content-Length", str(len(body))),
                        ("Cache-Control", "no-store"),
                    ]
                ),
                body if request.method == "GET" else b"",
            )

        if path == WS_PATH:
            return None

        return Response(
            404,
            "Not Found",
            Headers(cors_headers + [("Content-Length", "0")]),
            b"",
        )

    async def _handle_connection(self, connection: Any) -> None:
        request_path = getattr(getattr(connection, "request", None), "path", "")
        token = parse_qs(urlsplit(request_path).query).get("token", [""])[0]
        if not hmac.compare_digest(token, self.registry.discovery_token):
            await connection.close(WS_CLOSE_AUTH_FAILED, "auth failed")
            return

        client = self.registry.register_client()
        self._connections[client.session_id] = connection
        self._send_locks[client.session_id] = asyncio.Lock()
        try:
            async for raw in connection:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    frame = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(frame, dict):
                    await self._route_frame(client, frame)
        except Exception:
            pass
        finally:
            self.registry.unregister_client(client.session_id)
            self._connections.pop(client.session_id, None)
            self._send_locks.pop(client.session_id, None)
            self._fail_pending_for_session(
                client.session_id,
                "The browser extension disconnected.",
            )
            if not any(
                item.hello_received
                for item in self.registry._clients.values()
            ):
                self._ready.clear()

    async def _route_frame(
        self,
        client: BrowserClient,
        frame: dict[str, Any],
    ) -> None:
        kind = _normalize_frame_type(frame)
        payload = _frame_payload(frame)
        client.last_seen_at = _now_ms()

        if kind == "hello":
            browser_id = payload.get("browser_id")
            label = payload.get("browser_label")
            extension_version = ""
            info = payload.get("client")
            if isinstance(info, dict):
                extension_version = info.get("extension_version") or ""
            if isinstance(browser_id, str) and browser_id:
                updated = self.registry.set_browser_id(
                    client.session_id,
                    browser_id,
                    label if isinstance(label, str) else "",
                    extension_version if isinstance(extension_version, str) else "",
                )
                if updated is not None:
                    client = updated
                self._ready.set()
            message_id = frame.get("id")
            await self._send(
                client.session_id,
                {
                    "type": "hello_ack",
                    "id": message_id if isinstance(message_id, str) else "hello_ack",
                    "session_id": client.session_id,
                    "timestamp": _now_ms(),
                    "payload": {
                        "browser_id": client.browser_id,
                        "browser_label": client.label,
                    },
                },
            )
            return

        if kind == "page_updated":
            browser_id = payload.get("browser_id")
            tab_id = payload.get("tab_id")
            window_id = payload.get("window_id")
            if (
                isinstance(browser_id, str)
                and browser_id
                and isinstance(tab_id, (int, float))
                and isinstance(window_id, (int, float))
            ):
                self.registry.upsert_page(
                    browser_id,
                    int(window_id),
                    int(tab_id),
                    payload.get("url") if isinstance(payload.get("url"), str) else "",
                    payload.get("title") if isinstance(payload.get("title"), str) else "",
                    bool(payload.get("active")),
                    payload.get("context_role")
                    if isinstance(payload.get("context_role"), str)
                    else "",
                )
            return

        if kind == "page_removed":
            browser_id = payload.get("browser_id")
            tab_id = payload.get("tab_id")
            if isinstance(browser_id, str) and isinstance(tab_id, (int, float)):
                self.registry.remove_page(browser_id, int(tab_id))
            return

        if kind == "result":
            message_id = frame.get("id")
            if isinstance(message_id, str):
                self._resolve_pending(message_id, payload)
            return

        if kind == "error":
            message_id = frame.get("id")
            if isinstance(message_id, str):
                message = payload.get("message")
                code = payload.get("code")
                detail = (
                    f"{code}: {message}"
                    if isinstance(code, str) and code and isinstance(message, str)
                    else message
                    if isinstance(message, str)
                    else "Browser bridge command failed."
                )
                self._reject_pending(
                    message_id,
                    BrowserBridgeError("EXTENSION_ERROR", detail),
                )

    async def _send(self, session_id: str, message: dict[str, Any]) -> None:
        connection = self._connections.get(session_id)
        lock = self._send_locks.get(session_id)
        if connection is None or lock is None:
            raise BrowserBridgeError(
                "DISCONNECTED",
                "The browser extension is not connected.",
            )
        async with lock:
            await connection.send(
                json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            )

    def _resolve_pending(self, command_id: str, payload: dict[str, Any]) -> None:
        entry = self._pending.pop(command_id, None)
        if entry is None:
            return
        _, future = entry
        if not future.done():
            future.set_result(payload)

    def _reject_pending(self, command_id: str, error: Exception) -> None:
        entry = self._pending.pop(command_id, None)
        if entry is None:
            return
        _, future = entry
        if not future.done():
            future.set_exception(error)

    def _fail_pending_for_session(self, session_id: str, message: str) -> None:
        for command_id, (owner, future) in list(self._pending.items()):
            if owner != session_id:
                continue
            self._pending.pop(command_id, None)
            if not future.done():
                future.set_exception(
                    BrowserBridgeError("DISCONNECTED", message)
                )

    def extension_status(self) -> dict[str, Any]:
        sessions = self.registry.list_sessions()
        return {
            "ready": bool(sessions),
            "sessions_count": len(sessions),
            "browser_ids": [item["browserId"] for item in sessions],
            "extension_versions": self.registry.extension_versions(),
        }

    async def wait_until_ready(
        self,
        timeout: float,
        cancel_event: asyncio.Event | None = None,
    ) -> bool:
        if self.extension_status()["ready"]:
            return True
        if timeout <= 0:
            return False
        ready_task = asyncio.create_task(self._ready.wait())
        cancel_task: asyncio.Task[bool] | None = None
        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
        tasks = {ready_task}
        if cancel_task is not None:
            tasks.add(cancel_task)
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if cancel_task is not None and cancel_task in done:
            return False
        return ready_task in done

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.registry.list_sessions()

    def list_pages(self, browser_id: str) -> list[dict[str, Any]]:
        return self.registry.list_pages(browser_id)

    def _require_page(self, browser_id: str, page_id: str) -> BrowserPage:
        page = self.registry.get_page(browser_id, page_id)
        if page is None:
            raise BrowserBridgeError(
                "PAGE_NOT_FOUND",
                f"page_id {page_id} not found under browser_id {browser_id}; "
                "call list_pages again.",
            )
        return page

    async def _command(
        self,
        browser_id: str,
        tool: str,
        arguments: dict[str, Any],
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        client = self.registry.client_by_browser(browser_id)
        if client is None:
            raise BrowserBridgeError(
                "BROWSER_NOT_CONNECTED",
                f"browser_id {browser_id} is not connected.",
            )
        command_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[command_id] = (client.session_id, future)
        try:
            await self._send(
                client.session_id,
                {
                    "type": "command",
                    "id": command_id,
                    "session_id": client.session_id,
                    "timestamp": _now_ms(),
                    "payload": {"tool": tool, "arguments": arguments},
                },
            )
            ready_task = asyncio.ensure_future(future)
            cancel_task: asyncio.Task[bool] | None = None
            if cancel_event is not None:
                cancel_task = asyncio.create_task(cancel_event.wait())
            tasks = {ready_task}
            if cancel_task is not None:
                tasks.add(cancel_task)
            try:
                done, _ = await asyncio.wait(
                    tasks,
                    timeout=self.command_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if cancel_task is not None and not cancel_task.done():
                    cancel_task.cancel()
                if not ready_task.done():
                    ready_task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if cancel_task is not None and cancel_task in done:
                raise BrowserBridgeError(
                    "CANCELLED",
                    "The browser command was cancelled.",
                )
            if ready_task not in done:
                raise BrowserBridgeError(
                    "COMMAND_TIMEOUT",
                    f"Browser command {tool} timed out after "
                    f"{self.command_timeout:g}s.",
                )
            return ready_task.result()
        finally:
            self._pending.pop(command_id, None)

    async def open_tab(
        self,
        browser_id: str,
        url: str,
        active: bool = True,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        return await self._command(
            browser_id,
            "browser_open_tab",
            {"browser_id": browser_id, "url": url, "active": active},
            cancel_event,
        )

    async def focus_page(
        self,
        browser_id: str,
        page_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_focus_page",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "window_id": page.window_id,
                "tab_id": page.tab_id,
            },
            cancel_event,
        )

    async def close_tab(
        self,
        browser_id: str,
        page_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_close_tab",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
            },
            cancel_event,
        )

    async def read_state(
        self,
        browser_id: str,
        page_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_read_state",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "task_id": "",
            },
            cancel_event,
        )

    async def click(
        self,
        browser_id: str,
        page_id: str,
        index: int,
        variant: str = "click",
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_click",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "index": index,
                "variant": variant,
            },
            cancel_event,
        )

    async def type_text(
        self,
        browser_id: str,
        page_id: str,
        index: int,
        text: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_type",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "index": index,
                "text": text,
            },
            cancel_event,
        )

    async def press(
        self,
        browser_id: str,
        page_id: str,
        key: str,
        index: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        arguments: dict[str, Any] = {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "key": key,
        }
        if index is not None and index > 0:
            arguments["index"] = index
        return await self._command(
            browser_id,
            "browser_press",
            arguments,
            cancel_event,
        )

    async def scroll(
        self,
        browser_id: str,
        page_id: str,
        x: int,
        y: int,
        index: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        arguments: dict[str, Any] = {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "x": x,
            "y": y,
        }
        if index is not None and index > 0:
            arguments["index"] = index
        return await self._command(
            browser_id,
            "browser_scroll",
            arguments,
            cancel_event,
        )

    async def wait_for(
        self,
        browser_id: str,
        page_id: str,
        timeout_ms: int = 10000,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_wait_for",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "timeout_ms": timeout_ms,
            },
            cancel_event,
        )

    async def go_back(
        self,
        browser_id: str,
        page_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_go_back",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
            },
            cancel_event,
        )

    async def reload(
        self,
        browser_id: str,
        page_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_reload",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
            },
            cancel_event,
        )

    async def extract(
        self,
        browser_id: str,
        page_id: str,
        index: int,
        include_html: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_extract",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "index": index,
                "include_html": include_html,
            },
            cancel_event,
        )

    async def describe_element(
        self,
        browser_id: str,
        page_id: str,
        index: int,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_describe_element",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "index": index,
            },
            cancel_event,
        )

    async def execute_script(
        self,
        browser_id: str,
        page_id: str,
        script: str,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        page = self._require_page(browser_id, page_id)
        return await self._command(
            browser_id,
            "browser_execute_script",
            {
                "browser_id": browser_id,
                "page_id": page_id,
                "tab_id": page.tab_id,
                "script": script,
            },
            cancel_event,
        )


def classify_browser_action(action: str) -> Literal["readonly", "mutating", "script"]:
    if action in READONLY_ACTIONS:
        return "readonly"
    if action in SCRIPT_ACTIONS:
        return "script"
    return "mutating"


__all__ = [
    "BrowserBridge",
    "BrowserBridgeError",
    "BrowserClient",
    "BrowserPage",
    "BrowserRegistry",
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "DEFAULT_CONNECT_WAIT_SECONDS",
    "DEFAULT_PORT",
    "MUTATING_ACTIONS",
    "PING_PATH",
    "PING_SIGNATURE",
    "PING_SIG_HEADER",
    "READONLY_ACTIONS",
    "SCRIPT_ACTIONS",
    "WS_CLOSE_AUTH_FAILED",
    "WS_PATH",
    "classify_browser_action",
]

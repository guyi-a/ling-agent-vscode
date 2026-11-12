"""Browser tools backed by the Kro extension bridge."""

from __future__ import annotations

import json
from typing import Any

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..browser_bridge import (
    BrowserBridge,
    BrowserBridgeError,
    MAX_RESULT_BYTES,
)
from ..config import HarnessConfig

BROWSER_ACTIONS = (
    "extension_status",
    "list_sessions",
    "list_pages",
    "open_tab",
    "focus_page",
    "close_tab",
    "read_state",
    "click",
    "hover",
    "dblclick",
    "rightclick",
    "type",
    "press",
    "scroll",
    "wait_for",
    "go_back",
    "reload",
    "extract",
    "describe_element",
    "execute_script",
)


def _string_arg(
    call: ToolCall,
    name: str,
    *,
    required: bool = True,
    default: str = "",
) -> str:
    value = call.arguments.get(name)
    if value is None:
        if required:
            raise ValueError(f"{name} is required for {call.arguments.get('action')}")
        return default
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _int_arg(
    call: ToolCall,
    name: str,
    *,
    required: bool = False,
    default: int = 0,
    minimum: int | None = None,
) -> int:
    value = call.arguments.get(name)
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _bool_arg(call: ToolCall, name: str, default: bool = False) -> bool:
    value = call.arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _cap_json(payload: dict[str, Any], action: str) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_RESULT_BYTES:
        return text
    preview = encoded[: MAX_RESULT_BYTES - 512].decode(
        "utf-8",
        errors="ignore",
    )
    stub = {
        "ok": payload.get("ok", True),
        "truncated": True,
        "action": action,
        "original_size_bytes": len(encoded),
        "kept_size_bytes": len(preview.encode("utf-8")),
        "note": (
            f"result was {len(encoded)} bytes and exceeded "
            f"{MAX_RESULT_BYTES}; showing a JSON prefix. Narrow the "
            "request instead of retrying the same call."
        ),
        "preview_json": preview,
    }
    return json.dumps(stub, ensure_ascii=False, separators=(",", ":"))


def _details(
    action: str,
    call: ToolCall,
) -> dict[str, Any]:
    details: dict[str, Any] = {"kind": "browser", "action": action}
    for key, output_key in (
        ("browser_id", "browserId"),
        ("page_id", "pageId"),
        ("url", "url"),
    ):
        value = call.arguments.get(key)
        if isinstance(value, str) and value:
            details[output_key] = value
    return details


def create_browser_tools(
    bridge: BrowserBridge,
    config: HarnessConfig,
) -> list[AgentTool]:
    async def browser_bridge(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        details: dict[str, Any] = {"kind": "browser"}
        action = ""
        try:
            action = _string_arg(call, "action")
            details = _details(action, call)
            if action not in BROWSER_ACTIONS:
                raise ValueError(
                    "unknown browser action; expected one of: "
                    + ", ".join(BROWSER_ACTIONS)
                )
            await bridge.ensure_started()
            if action == "extension_status":
                await bridge.wait_until_ready(
                    config.browser_bridge_connect_wait_seconds,
                    context.cancel_event,
                )
                payload: dict[str, Any] = {
                    "ok": True,
                    "status": bridge.extension_status(),
                }
            elif action == "list_sessions":
                payload = {
                    "ok": True,
                    "sessions": bridge.list_sessions(),
                }
            elif action == "list_pages":
                browser_id = _string_arg(call, "browser_id")
                payload = {
                    "ok": True,
                    "pages": bridge.list_pages(browser_id),
                }
            elif action == "open_tab":
                browser_id = _string_arg(call, "browser_id")
                url = _string_arg(call, "url")
                payload = {
                    "ok": True,
                    "data": await bridge.open_tab(
                        browser_id,
                        url,
                        _bool_arg(call, "active", True),
                        context.cancel_event,
                    ),
                }
            elif action == "focus_page":
                payload = {
                    "ok": True,
                    "data": await bridge.focus_page(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        context.cancel_event,
                    ),
                }
            elif action == "close_tab":
                payload = {
                    "ok": True,
                    "data": await bridge.close_tab(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        context.cancel_event,
                    ),
                }
            elif action == "read_state":
                payload = {
                    "ok": True,
                    "data": await bridge.read_state(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        context.cancel_event,
                    ),
                }
            elif action in ("click", "hover", "dblclick", "rightclick"):
                payload = {
                    "ok": True,
                    "data": await bridge.click(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _int_arg(call, "index", required=True, minimum=1),
                        action,
                        context.cancel_event,
                    ),
                }
            elif action == "type":
                payload = {
                    "ok": True,
                    "data": await bridge.type_text(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _int_arg(call, "index", required=True, minimum=1),
                        _string_arg(call, "text"),
                        context.cancel_event,
                    ),
                }
            elif action == "press":
                index = call.arguments.get("index")
                payload = {
                    "ok": True,
                    "data": await bridge.press(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _string_arg(call, "key"),
                        _int_arg(call, "index", minimum=1)
                        if index is not None
                        else None,
                        context.cancel_event,
                    ),
                }
            elif action == "scroll":
                index = call.arguments.get("index")
                payload = {
                    "ok": True,
                    "data": await bridge.scroll(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _int_arg(call, "x"),
                        _int_arg(call, "y"),
                        _int_arg(call, "index", minimum=1)
                        if index is not None
                        else None,
                        context.cancel_event,
                    ),
                }
            elif action == "wait_for":
                timeout_ms = _int_arg(
                    call,
                    "timeout_ms",
                    default=10_000,
                    minimum=1,
                )
                payload = {
                    "ok": True,
                    "data": await bridge.wait_for(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        timeout_ms,
                        context.cancel_event,
                    ),
                }
            elif action in ("go_back", "reload"):
                method = (
                    bridge.go_back if action == "go_back" else bridge.reload
                )
                payload = {
                    "ok": True,
                    "data": await method(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        context.cancel_event,
                    ),
                }
            elif action == "extract":
                payload = {
                    "ok": True,
                    "data": await bridge.extract(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _int_arg(call, "index", required=True, minimum=1),
                        _bool_arg(call, "include_html"),
                        context.cancel_event,
                    ),
                }
            elif action == "describe_element":
                payload = {
                    "ok": True,
                    "data": await bridge.describe_element(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _int_arg(call, "index", required=True, minimum=1),
                        context.cancel_event,
                    ),
                }
            else:  # execute_script
                payload = {
                    "ok": True,
                    "data": await bridge.execute_script(
                        _string_arg(call, "browser_id"),
                        _string_arg(call, "page_id"),
                        _string_arg(call, "script"),
                        context.cancel_event,
                    ),
                }
            return ToolExecution(
                output=_cap_json(payload, action),
                details=details,
            )
        except (BrowserBridgeError, ValueError) as exc:
            message = str(exc)
            code = getattr(exc, "code", "INVALID_ARGUMENT")
            return ToolExecution(
                output=json.dumps(
                    {"ok": False, "code": code, "message": message},
                    ensure_ascii=False,
                ),
                is_error=True,
                details=details,
            )

    return [
        AgentTool(
            definition=Tool(
                name="browser_bridge",
                description=(
                    "Drive the user's real Edge browser through the Kro "
                    "Browser Bridge extension. Always start with "
                    "action='extension_status'; if ready=false, stop and ask "
                    "the user to connect the extension. Typical flow: "
                    "extension_status -> list_sessions -> list_pages or "
                    "open_tab -> read_state (get element indices) -> "
                    "click/type/press by index -> read_state again. Element "
                    "indices expire whenever the DOM changes. Never close "
                    "the user's tabs or run arbitrary scripts unless "
                    "explicitly requested; hand login, CAPTCHA, payment, "
                    "and consent screens back to the user."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(BROWSER_ACTIONS),
                        },
                        "browser_id": {"type": "string"},
                        "page_id": {"type": "string"},
                        "url": {"type": "string"},
                        "active": {"type": "boolean", "default": True},
                        "index": {"type": "integer", "minimum": 1},
                        "text": {"type": "string"},
                        "key": {"type": "string"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "timeout_ms": {"type": "integer", "minimum": 1},
                        "include_html": {"type": "boolean", "default": False},
                        "script": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ),
            handler=browser_bridge,
        )
    ]


__all__ = ["BROWSER_ACTIONS", "create_browser_tools"]

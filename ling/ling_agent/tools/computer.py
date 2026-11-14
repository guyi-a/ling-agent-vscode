"""Computer-use tools backed by the Windows helper."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ling_ai.types import (
    ImageContent,
    TextContent,
    Tool,
    ToolCall,
)
from ling_core import AgentTool, ToolContext, ToolExecution

from ..computer.service import ComputerService
from ..computer.types import ComputerError
from ..config import HarnessConfig


def _string_arg(
    call: ToolCall,
    name: str,
    *,
    default: str = "",
    required: bool = False,
) -> str:
    value = call.arguments.get(name)
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return default
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if required and not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _string_list_arg(
    call: ToolCall,
    name: str,
) -> list[str]:
    value = call.arguments.get(name)
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a list of strings")
    return value


def _bool_arg(call: ToolCall, name: str, default: bool = False) -> bool:
    value = call.arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _int_arg(
    call: ToolCall,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = call.arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


_COORDINATE_ACTIONS = frozenset({"click_at", "scroll_at", "drag_at"})
_REF_ACTIONS = frozenset(
    {
        "click",
        "set_value",
        "focus",
        "toggle",
        "expand",
        "collapse",
        "select",
        "scroll",
        "drag",
    }
)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_actions(
    actions: list[dict[str, Any]],
    snapshot_id: str,
    capture_id: str,
    observation_id: str = "",
) -> None:
    if not observation_id and not snapshot_id and not capture_id:
        raise ValueError("observation_id is required")
    needs_snapshot = False
    needs_capture = False
    for index, action in enumerate(actions, 1):
        kind = action.get("type")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"action {index} requires a type")
        if kind in _COORDINATE_ACTIONS:
            needs_capture = True
            if not (_is_int(action.get("x")) and _is_int(action.get("y"))):
                raise ValueError(
                    f"action {index} ({kind}) requires integer x and y"
                )
            if kind == "drag_at" and not (
                _is_int(action.get("to_x"))
                and _is_int(action.get("to_y"))
            ):
                raise ValueError(
                    f"action {index} (drag_at) requires integer to_x and to_y"
                )
        elif kind == "type":
            has_x = "x" in action
            has_y = "y" in action
            if has_x or has_y:
                if not (
                    has_x
                    and has_y
                    and _is_int(action.get("x"))
                    and _is_int(action.get("y"))
                ):
                    raise ValueError(
                        f"action {index} (type) requires integer x and y"
                    )
                needs_capture = True
            else:
                needs_snapshot = True
                if not action.get("ref"):
                    raise ValueError(
                        f"action {index} (type) requires ref or x/y"
                    )
        elif kind == "press_key":
            if action.get("ref"):
                needs_snapshot = True
        elif kind in _REF_ACTIONS:
            needs_snapshot = True
            if not action.get("ref"):
                raise ValueError(
                    f"action {index} ({kind}) requires a ref"
                )
        else:
            raise ValueError(
                f"action {index} has unsupported type: {kind}"
            )
    if needs_snapshot and not snapshot_id and not observation_id:
        raise ValueError(
            "snapshot_id is required for actions that use UIA refs"
        )
    if needs_capture and not capture_id and not observation_id:
        raise ValueError(
            "capture_id is required for coordinate actions"
        )


def _ok_execution(
    result: dict[str, Any],
    details: dict[str, Any],
) -> ToolExecution:
    output = json.dumps({"ok": True, **result}, ensure_ascii=False)
    merged = {"kind": "computer", **details}
    preview = merged.get("preview")
    if not isinstance(preview, str) or not preview:
        merged["preview"] = output[:1500]
    return ToolExecution(output=output, details=merged)


def _error_execution(
    operation: str,
    exc: Exception,
    window_id: str = "",
) -> ToolExecution:
    if isinstance(exc, ComputerError):
        payload = {"ok": False, **exc.to_json()}
        code = exc.code
        message = exc.message
    else:
        payload = {
            "ok": False,
            "code": "COMPUTER_ERROR",
            "message": str(exc),
            "hint": "",
            "mayHaveTakenEffect": False,
        }
        code = "COMPUTER_ERROR"
        message = str(exc)
    output = json.dumps(payload, ensure_ascii=False)
    details: dict[str, Any] = {
        "kind": "computer",
        "operation": operation,
        "error": code,
        "preview": message,
    }
    if window_id:
        details["windowId"] = window_id
    return ToolExecution(
        output=output,
        is_error=True,
        details=details,
    )


def create_computer_tools(
    service: ComputerService,
    config: HarnessConfig,
) -> list[AgentTool]:
    """Create the read-only computer inspection tools."""

    async def computer_list(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            include_all = _bool_arg(call, "include_all")
            query = _string_arg(call, "query")
            result = await service.list_windows(
                include_all=include_all,
                query=query,
            )
            return _ok_execution(
                result,
                {
                    "operation": "list",
                    "windowCount": len(result.get("windows", [])),
                    "displayCount": len(result.get("displays", [])),
                    "truncated": bool(result.get("truncated")),
                },
            )
        except Exception as exc:
            return _error_execution("list", exc)

    async def computer_observe(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        window_id = ""
        try:
            window_id = _string_arg(
                call,
                "window_id",
                required=True,
            )
            include_screenshot = _bool_arg(
                call,
                "include_screenshot",
                True,
            )
            include_text = _bool_arg(
                call,
                "include_text",
                True,
            )
            max_width = _int_arg(
                call,
                "max_width",
                1280,
                320,
                3840,
            )
            max_depth = _int_arg(call, "max_depth", 8, 1, 50)
            max_nodes = _int_arg(call, "max_nodes", 300, 1, 5000)
            max_chars = _int_arg(
                call,
                "max_chars",
                30_000,
                1_000,
                200_000,
            )
            query = _string_arg(call, "query")
            result = await service.observe(
                window_id=window_id,
                include_screenshot=include_screenshot,
                include_text=include_text,
                max_width=max_width,
                max_depth=max_depth,
                max_nodes=max_nodes,
                max_chars=max_chars,
                query=query,
                call_id=call.id,
            )
            window = (
                result.get("window")
                if isinstance(result.get("window"), dict)
                else {}
            )
            node_count = result.get("nodeCount")
            details = {
                "operation": "observe",
                "windowId": window_id,
                "title": str(window.get("title", "")),
                "app": str(window.get("app", "")),
                "observationId": str(
                    result.get("observationId", "")
                ),
                "snapshotId": str(result.get("snapshotId", "")),
                "captureId": str(result.get("captureId", "")),
                "nodeCount": (
                    node_count
                    if isinstance(node_count, int)
                    and not isinstance(node_count, bool)
                    else 0
                ),
                "complete": bool(result.get("complete")),
                "truncated": bool(result.get("truncated")),
                "preview": str(result.get("tree", ""))[:1200],
            }
            path_value = result.get("path")
            if not isinstance(path_value, str) or not path_value:
                return _ok_execution(result, details)
            path = Path(path_value)
            data = path.read_bytes()
            if len(data) > 4_000_000:
                raise ComputerError(
                    "CAPTURE_TOO_LARGE",
                    "The screenshot is too large; reduce max_width.",
                )
            return ToolExecution(
                output=[
                    TextContent(
                        json.dumps(
                            {"ok": True, **result},
                            ensure_ascii=False,
                        )
                    ),
                    ImageContent(
                        data=base64.b64encode(data).decode("ascii"),
                        mime_type="image/png",
                    ),
                ],
                details={"kind": "computer", **details},
            )
        except Exception as exc:
            return _error_execution(
                "observe",
                exc,
                window_id=window_id,
            )

    async def computer_screenshot(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        window_id = ""
        try:
            window_id = _string_arg(
                call,
                "window_id",
                required=True,
            )
            max_width = _int_arg(
                call,
                "max_width",
                1280,
                320,
                3840,
            )
            result = await service.screenshot(
                window_id=window_id,
                max_width=max_width,
                call_id=call.id,
            )
            path_value = result.get("path")
            if not isinstance(path_value, str) or not path_value:
                raise ComputerError(
                    "CAPTURE_NOT_FOUND",
                    "The screenshot file was not produced.",
                )
            path = Path(path_value)
            data = path.read_bytes()
            if len(data) > 4_000_000:
                raise ComputerError(
                    "CAPTURE_TOO_LARGE",
                    "The screenshot is too large; reduce max_width.",
                )
            window = (
                result.get("window")
                if isinstance(result.get("window"), dict)
                else {}
            )
            title = str(window.get("title", ""))
            metadata = {
                "ok": True,
                "captureId": str(result.get("captureId", "")),
                "windowId": window_id,
                "title": title,
                "app": str(window.get("app", "")),
                "width": result.get("width"),
                "height": result.get("height"),
                "originalWidth": result.get("originalWidth"),
                "originalHeight": result.get("originalHeight"),
                "scale": result.get("scale"),
                "method": str(result.get("method", "")),
                "path": str(path),
            }
            return ToolExecution(
                output=[
                    TextContent(
                        json.dumps(metadata, ensure_ascii=False)
                    ),
                    ImageContent(
                        data=base64.b64encode(data).decode("ascii"),
                        mime_type="image/png",
                    ),
                ],
                details={
                    "kind": "computer",
                    "operation": "screenshot",
                    "windowId": window_id,
                    "title": title,
                    "app": str(window.get("app", "")),
                    "captureId": str(result.get("captureId", "")),
                    "width": result.get("width"),
                    "height": result.get("height"),
                    "scale": result.get("scale"),
                    "method": str(result.get("method", "")),
                    "complete": True,
                    "preview": (
                        f"Screenshot {title or window_id} "
                        f"{result.get('width')}x{result.get('height')}"
                    ),
                },
            )
        except Exception as exc:
            return _error_execution(
                "screenshot",
                exc,
                window_id=window_id,
            )

    async def computer_focus(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        window_id = ""
        try:
            window_id = _string_arg(
                call,
                "window_id",
                required=True,
            )
            result = await service.focus(
                window_id=window_id,
                call_id=call.id,
            )
            window = (
                result.get("window")
                if isinstance(result.get("window"), dict)
                else {}
            )
            title = str(window.get("title", ""))
            foreground = bool(result.get("foreground"))
            restored = bool(result.get("restored"))
            preview = (
                f"Focused {title or window_id}"
                f" (foreground={str(foreground).lower()}, "
                f"restored={str(restored).lower()})"
            )
            return _ok_execution(
                result,
                {
                    "operation": "focus",
                    "windowId": window_id,
                    "title": title,
                    "app": str(window.get("app", "")),
                    "foreground": foreground,
                    "restored": restored,
                    "complete": foreground,
                    "preview": preview[:1200],
                },
            )
        except Exception as exc:
            return _error_execution(
                "focus",
                exc,
                window_id=window_id,
            )

    async def computer_launch(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        app = ""
        try:
            app = _string_arg(call, "app", required=True)
            arguments = _string_list_arg(call, "arguments")
            wait_for_window_ms = _int_arg(
                call,
                "wait_for_window_ms",
                5000,
                0,
                15_000,
            )
            result = await service.launch(
                app=app,
                arguments=arguments,
                wait_for_window_ms=wait_for_window_ms,
                call_id=call.id,
            )
            window = (
                result.get("window")
                if isinstance(result.get("window"), dict)
                else {}
            )
            window_id = str(window.get("id", ""))
            title = str(window.get("title", ""))
            verb = "Reused" if result.get("reused") else "Launched"
            preview = (
                f"{verb} {app} (pid {result.get('pid') or '?'})"
                + (f" -> {title or window_id}" if window_id else "")
                + (" [timed out]" if result.get("timedOut") else "")
                + (
                    " · window hidden to tray"
                    if result.get("hint")
                    else ""
                )
            )
            return _ok_execution(
                result,
                {
                    "operation": "launch",
                    "app": app,
                    "title": title,
                    "windowId": window_id,
                    "pid": (
                        result.get("pid")
                        if isinstance(result.get("pid"), int)
                        else 0
                    ),
                    "timedOut": bool(result.get("timedOut")),
                    "reused": bool(result.get("reused")),
                    "complete": bool(window_id),
                    "preview": preview,
                },
            )
        except Exception as exc:
            return _error_execution(
                "launch",
                exc,
                window_id="",
            )

    async def computer_act(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        window_id = ""
        try:
            window_id = _string_arg(
                call,
                "window_id",
                required=True,
            )
            observation_id = _string_arg(
                call,
                "observation_id",
                default="",
            )
            snapshot_id = _string_arg(
                call,
                "snapshot_id",
                default="",
            )
            capture_id = _string_arg(
                call,
                "capture_id",
                default="",
            )
            action = call.arguments.get("action")
            actions = call.arguments.get("actions")
            if action is not None and actions is None:
                actions = [action]
            if (
                not isinstance(actions, list)
                or len(actions) != 1
            ):
                raise ValueError(
                    "exactly one action is required per observation"
                )
            if not all(isinstance(action, dict) for action in actions):
                raise ValueError("each action must be an object")
            _validate_actions(
                actions,
                snapshot_id,
                capture_id,
                observation_id,
            )
            result = await service.act(
                window_id=window_id,
                observation_id=observation_id,
                snapshot_id=snapshot_id,
                capture_id=capture_id,
                actions=actions,
                call_id=call.id,
            )
            steps = result.get("actions")
            preview_lines: list[str] = []
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    target = step.get("ref") or ""
                    if not target and "x" in step:
                        target = f"({step.get('x')},{step.get('y')})"
                    clipboard_note = ""
                    if step.get("clipboardReplaced"):
                        clipboard_note = " clipboard-replaced"
                    elif step.get("clipboardSkipped"):
                        clipboard_note = (
                            f" clipboard-skipped={step.get('clipboardSkipped')}"
                        )
                    preview_lines.append(
                        f"{step.get('type', '?')} "
                        f"{target} "
                        f"ok={step.get('ok')} "
                        f"via={step.get('mechanism', '?')} "
                        f"verified={step.get('verified')}"
                        f"{clipboard_note}"
                    )
            if not preview_lines:
                preview_lines.append(
                    json.dumps(result, ensure_ascii=False)[:1200]
                )
            window = (
                result.get("window")
                if isinstance(result.get("window"), dict)
                else {}
            )
            first = (
                actions[0].get("type", "")
                if isinstance(actions[0], dict)
                else ""
            )
            summary = (
                str(first)
                if isinstance(first, str) and first
                else "actions"
            )
            observation = (
                result.get("observation")
                if isinstance(result.get("observation"), dict)
                else {}
            )
            details = {
                "operation": "act",
                "windowId": window_id,
                "title": str(window.get("title", "")),
                "app": str(window.get("app", "")),
                "actionSummary": summary,
                "observationId": str(
                    observation.get("observationId", "")
                ),
                "snapshotId": str(
                    observation.get("snapshotId", snapshot_id)
                ),
                "captureId": str(
                    observation.get("captureId", capture_id)
                ),
                "complete": bool(result.get("delivered", False)),
                "preview": "\n".join(preview_lines)[:1200],
            }
            path_value = observation.get("path")
            if not isinstance(path_value, str) or not path_value:
                return _ok_execution(result, details)
            path = Path(path_value)
            data = path.read_bytes()
            if len(data) > 4_000_000:
                raise ComputerError(
                    "CAPTURE_TOO_LARGE",
                    "The refreshed screenshot is too large.",
                )
            return ToolExecution(
                output=[
                    TextContent(
                        json.dumps(
                            {"ok": True, **result},
                            ensure_ascii=False,
                        )
                    ),
                    ImageContent(
                        data=base64.b64encode(data).decode("ascii"),
                        mime_type="image/png",
                    ),
                ],
                details={"kind": "computer", **details},
            )
        except Exception as exc:
            return _error_execution(
                "act",
                exc,
                window_id=window_id,
            )

    async def computer_stop(
        call: ToolCall,
        _context: ToolContext,
    ) -> ToolExecution:
        try:
            reason = _string_arg(call, "reason", default="tool")
            stopped = await service.stop(reason or "tool")
            return _ok_execution(
                {"stopped": stopped},
                {
                    "operation": "stop",
                    "preview": (
                        "Control session stopped."
                        if stopped
                        else "No active control session."
                    ),
                },
            )
        except Exception as exc:
            return _error_execution("stop", exc)

    return [
        AgentTool(
            definition=Tool(
                name="computer_list",
                description=(
                    "List top-level Windows and displays. Read-only. "
                    "Returns window ids, process identity, title, bounds, "
                    "and whether computer-use policy allows control. "
                    "Use computer_observe with a window id to inspect its "
                    "UI Automation tree. Terminals, password managers, "
                    "UAC, and elevated surfaces are marked uncontrollable."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "include_all": {
                            "type": "boolean",
                            "description": (
                                "Include hidden or tool windows for "
                                "diagnostics. Defaults to false. If an "
                                "app is running but no window is "
                                "visible, set this to true to find its "
                                "tray/hidden window instead of using "
                                "Get-Process."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Case-insensitive filter on title, "
                                "executable, or window class."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            handler=computer_list,
        ),
        AgentTool(
            definition=Tool(
                name="computer_observe",
                description=(
                    "Observe one window as a single point-in-time state. "
                    "Read-only. By default returns a screenshot, "
                    "observation id, focused element, document text, "
                    "UIA refs, and a bounded tree. Pass that observation "
                    "id to exactly one computer_act call."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "window_id": {
                            "type": "string",
                            "description": (
                                "Window id from computer_list, for "
                                "example w-123456."
                            ),
                        },
                        "include_screenshot": {
                            "type": "boolean",
                            "description": (
                                "Include a window screenshot. Defaults "
                                "to true."
                            ),
                        },
                        "include_text": {
                            "type": "boolean",
                            "description": (
                                "Include the UIA tree and element refs. "
                                "Defaults to true."
                            ),
                        },
                        "max_width": {
                            "type": "integer",
                            "minimum": 320,
                            "maximum": 3840,
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                        },
                        "max_nodes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5000,
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 200000,
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "Optional case-insensitive filter on "
                                "element name, value, id, or role."
                            ),
                        },
                    },
                    "required": ["window_id"],
                    "additionalProperties": False,
                },
            ),
            handler=computer_observe,
        ),
        AgentTool(
            definition=Tool(
                name="computer_screenshot",
                description=(
                    "Capture a PNG screenshot of one window and return "
                    "the image. Read-only; no takeover approval. Use "
                    "this when the UI Automation tree is shallow, "
                    "empty, or visually ambiguous, or for custom-drawn "
                    "apps. Terminals, password managers, the lock "
                    "screen, and elevated windows are blocked. Call "
                    "computer_list first to get a window id."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "window_id": {
                            "type": "string",
                            "description": "Window id from computer_list.",
                        },
                        "max_width": {
                            "type": "integer",
                            "minimum": 320,
                            "maximum": 3840,
                        },
                    },
                    "required": ["window_id"],
                    "additionalProperties": False,
                },
            ),
            handler=computer_screenshot,
        ),
        AgentTool(
            definition=Tool(
                name="computer_focus",
                description=(
                    "Restore and bring an existing window to the "
                    "foreground. Use this instead of PowerShell/"
                    "Add-Type window scripts. Works on minimized or "
                    "hidden windows and does not need a snapshot. "
                    "Requires an approved control session; after the "
                    "takeover approval it does not prompt again. If "
                    "foreground is false, ask the user to click the "
                    "window or open it from the tray."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "window_id": {
                            "type": "string",
                            "description": "Window id from computer_list.",
                        },
                    },
                    "required": ["window_id"],
                    "additionalProperties": False,
                },
            ),
            handler=computer_focus,
        ),
        AgentTool(
            definition=Tool(
                name="computer_launch",
                description=(
                    "Open a GUI application and return its window. Use "
                    "this instead of run_command for native apps. If the "
                    "app is already running (including a window hidden "
                    "to the tray), it restores and reuses that window "
                    "instead of starting a second instance. The app is "
                    "resolved against Windows system paths and App "
                    "Paths, so a Git/Unix shim named notepad cannot "
                    "shadow notepad.exe. Pass arguments only when you "
                    "need a new file/window. Covered by the takeover "
                    "approval; terminals and password managers stay "
                    "blocked."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "app": {
                            "type": "string",
                            "description": (
                                "Executable name or full path, for "
                                "example notepad.exe or "
                                "C:\\Windows\\System32\\notepad.exe."
                            ),
                        },
                        "arguments": {
                            "type": "array",
                            "maxItems": 32,
                            "items": {"type": "string"},
                        },
                        "wait_for_window_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 15000,
                        },
                    },
                    "required": ["app"],
                    "additionalProperties": False,
                },
            ),
            handler=computer_launch,
        ),
        AgentTool(
            definition=Tool(
                name="computer_act",
                description=(
                    "Perform exactly one action from the latest "
                    "computer_observe result, then immediately return a "
                    "fresh observation. The first call requires one "
                    "takeover approval; after that the whole desktop is "
                    "covered until computer_stop, timeout, or user "
                    "takeover. Do not reuse an observation after acting. "
                    "UIA actions: click, set_value, type, press_key, "
                    "focus, toggle, expand, collapse, select, scroll, "
                    "drag. Coordinate actions: click_at, scroll_at, "
                    "drag_at, and type with x/y. Coordinates are pixels "
                    "in the observation screenshot. Use coordinate "
                    "actions for Qt, "
                    "Electron, and custom-drawn apps whose UIA tree has "
                    "no usable controls. press_key works without a ref "
                    "and goes to the focused control. Never reuse refs "
                    "from an older snapshot; observe again if the UI "
                    "changed."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "window_id": {
                            "type": "string",
                            "description": "Window id from computer_list.",
                        },
                        "observation_id": {
                            "type": "string",
                            "description": (
                                "Observation id from computer_observe. "
                                "Required for the normal one-action flow."
                            ),
                        },
                        "snapshot_id": {
                            "type": "string",
                            "description": (
                                "Snapshot id from the observation that "
                                "produced the refs. Required for actions "
                                "that use a ref; omit for coordinate-only "
                                "batches."
                            ),
                        },
                        "capture_id": {
                            "type": "string",
                            "description": (
                                "Capture id from computer_screenshot. "
                                "Required for click_at, scroll_at, "
                                "drag_at, and type with x/y."
                            ),
                        },
                        "actions": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "click",
                                            "click_at",
                                            "set_value",
                                            "type",
                                            "press_key",
                                            "focus",
                                            "toggle",
                                            "expand",
                                            "collapse",
                                            "select",
                                            "scroll",
                                            "scroll_at",
                                            "drag",
                                            "drag_at",
                                        ],
                                    },
                                    "ref": {
                                        "type": "string",
                                        "description": (
                                            "UIA ref for ref actions. "
                                            "Omit for click_at, "
                                            "scroll_at, drag_at, and "
                                            "type with x/y."
                                        ),
                                    },
                                    "value": {"type": "string"},
                                    "text": {
                                        "type": "string",
                                        "maxLength": 4000,
                                    },
                                    "key": {
                                        "type": "string",
                                        "description": (
                                            "Key name. A chord such as "
                                            "ctrl+s is accepted; the "
                                            "equivalent key plus "
                                            "modifiers form is preferred."
                                        ),
                                    },
                                    "modifiers": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "target_ref": {"type": "string"},
                                    "direction": {
                                        "type": "string",
                                        "enum": [
                                            "up",
                                            "down",
                                            "left",
                                            "right",
                                        ],
                                    },
                                    "amount": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 10,
                                    },
                                    "x": {
                                        "type": "integer",
                                        "description": (
                                            "Horizontal pixel in the "
                                            "screenshot image."
                                        ),
                                    },
                                    "y": {
                                        "type": "integer",
                                        "description": (
                                            "Vertical pixel in the "
                                            "screenshot image."
                                        ),
                                    },
                                    "to_x": {
                                        "type": "integer",
                                        "description": (
                                            "Drag end x in the "
                                            "screenshot image."
                                        ),
                                    },
                                    "to_y": {
                                        "type": "integer",
                                        "description": (
                                            "Drag end y in the "
                                            "screenshot image."
                                        ),
                                    },
                                    "button": {
                                        "type": "string",
                                        "enum": [
                                            "left",
                                            "right",
                                            "middle",
                                        ],
                                    },
                                    "clicks": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 3,
                                    },
                                },
                                "required": ["type"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": [
                        "window_id",
                        "observation_id",
                        "actions",
                    ],
                    "additionalProperties": False,
                },
            ),
            handler=computer_act,
        ),
        AgentTool(
            definition=Tool(
                name="computer_stop",
                description=(
                    "Stop the active computer control session and hide "
                    "the control overlay. Always available and never "
                    "requires approval."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            handler=computer_stop,
        ),
    ]


__all__ = ["create_computer_tools"]

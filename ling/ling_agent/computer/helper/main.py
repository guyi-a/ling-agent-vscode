"""Computer-use helper process entry point."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import input as input_api
from . import rpc
from .capture import capture_window
from .launch import focus_window, launch_app
from .overlay import ControlOverlay
from .uia import find_ref, run_actions, snapshot_window
from .win32 import list_windows, set_dpi_awareness, watch_parent
from ..types import COMPUTER_PROTOCOL_VERSION, ComputerError

_write_lock = threading.Lock()
_overlay: ControlOverlay | None = None
_ops = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="computer-op",
)
_control_stop = threading.Event()
_observations_lock = threading.Lock()
_observations: dict[str, dict[str, Any]] = {}
_OBSERVATION_TTL_SECONDS = 300.0

_LONG_METHODS = frozenset(
    {
        "window.observe",
        "uia.snapshot",
        "uia.find",
        "capture.window",
        "input.act",
        "app.launch",
    }
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--computer-helper", action="store_true")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--self-test", action="store_true")
    args, _ = parser.parse_known_args(argv)
    return args


def _write(payload: dict[str, Any]) -> None:
    with _write_lock:
        sys.stdout.write(rpc.encode_message(payload))
        sys.stdout.flush()


def _notify(method: str, params: dict[str, Any]) -> None:
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
    }
    with _write_lock:
        sys.stdout.write(rpc.encode_message(payload))
        sys.stdout.flush()


def _get_overlay() -> ControlOverlay:
    global _overlay
    if _overlay is None:
        _overlay = ControlOverlay()
    return _overlay


def _on_stop_requested() -> None:
    _control_stop.set()
    try:
        input_api.release_all()
    except OSError:
        pass
    _notify("control.stopRequested", {"reason": "hotkey"})


def _windows_list(params: dict[str, Any]) -> dict[str, Any]:
    include_all = params.get("include_all", False)
    if not isinstance(include_all, bool):
        raise ValueError("include_all must be a boolean")
    query = params.get("query", "")
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    max_windows = params.get("max_windows", 100)
    if not isinstance(max_windows, int) or isinstance(max_windows, bool):
        raise ValueError("max_windows must be an integer")
    return list_windows(
        include_all=include_all,
        query=query,
        max_windows=max_windows,
    )


def _str_param(
    params: dict[str, Any],
    name: str,
    default: str = "",
    *,
    required: bool = False,
) -> str:
    value = params.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if required and not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _int_param(
    params: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = params.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _str_list_param(
    params: dict[str, Any],
    name: str,
) -> list[str]:
    value = params.get(name) or []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{name} must be a list of strings")
    return value


def _bool_param(
    params: dict[str, Any],
    name: str,
    default: bool,
) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _remember_observation(
    *,
    snapshot_id: str,
    capture_id: str,
) -> str:
    observation_id = f"obs-{uuid.uuid4().hex[:12]}"
    now = time.time()
    with _observations_lock:
        stale = [
            key
            for key, value in _observations.items()
            if now - float(value.get("createdAt", 0))
            > _OBSERVATION_TTL_SECONDS
        ]
        for key in stale:
            _observations.pop(key, None)
        _observations[observation_id] = {
            "snapshotId": snapshot_id,
            "captureId": capture_id,
            "createdAt": now,
        }
    return observation_id


def _take_observation(observation_id: str) -> dict[str, Any]:
    if not isinstance(observation_id, str) or not observation_id:
        raise ComputerError(
            "INVALID_ARGS",
            "observation_id is required.",
        )
    now = time.time()
    with _observations_lock:
        value = _observations.pop(observation_id, None)
    if value is None or (
        now - float(value.get("createdAt", 0))
        > _OBSERVATION_TTL_SECONDS
    ):
        raise ComputerError(
            "STALE_OBSERVATION",
            "The observation is no longer available; observe again.",
        )
    return value


def _observe_window(params: dict[str, Any]) -> dict[str, Any]:
    window_id = _str_param(params, "window_id", required=True)
    include_screenshot = _bool_param(
        params,
        "include_screenshot",
        True,
    )
    include_text = _bool_param(params, "include_text", True)
    if not include_screenshot and not include_text:
        raise ValueError(
            "include_screenshot or include_text must be true"
        )
    snapshot: dict[str, Any] = {}
    capture: dict[str, Any] = {}
    text_error: ComputerError | None = None
    screenshot_error: ComputerError | None = None
    if include_text:
        try:
            snapshot = snapshot_window(
                window_id=window_id,
                max_depth=_int_param(params, "max_depth", 8, 1, 50),
                max_nodes=_int_param(
                    params,
                    "max_nodes",
                    300,
                    1,
                    5000,
                ),
                max_chars=_int_param(
                    params,
                    "max_chars",
                    30_000,
                    1_000,
                    200_000,
                ),
                query=_str_param(params, "query", ""),
            )
        except ComputerError as exc:
            text_error = exc
    if include_screenshot:
        try:
            capture = capture_window(
                window_id=window_id,
                max_width=_int_param(
                    params,
                    "max_width",
                    1280,
                    320,
                    3840,
                ),
            )
        except ComputerError as exc:
            screenshot_error = exc
    if not snapshot and not capture:
        error = text_error or screenshot_error
        if error is not None:
            raise error
        raise ComputerError(
            "OBSERVATION_FAILED",
            "Unable to observe the window.",
        )
    snapshot_window_info = snapshot.get("window")
    capture_window_info = capture.get("window")
    if isinstance(snapshot_window_info, dict) and isinstance(
        capture_window_info,
        dict,
    ):
        for name in ("id", "pid", "processCreationTime"):
            left = snapshot_window_info.get(name)
            right = capture_window_info.get(name)
            if (
                left is not None
                and right is not None
                and left != right
            ):
                raise ComputerError(
                    "WINDOW_CHANGED",
                    "The window changed during observation; observe again.",
                )
    observation_id = _remember_observation(
        snapshot_id=str(snapshot.get("snapshotId", "")),
        capture_id=str(capture.get("captureId", "")),
    )
    window = snapshot.get("window") or capture.get("window")
    result: dict[str, Any] = {
        "observationId": observation_id,
        "windowId": window_id,
        "window": window,
        "snapshotId": str(snapshot.get("snapshotId", "")),
        "captureId": str(capture.get("captureId", "")),
        "tree": str(snapshot.get("tree", "")),
        "elements": snapshot.get("elements", []),
        "focusedElement": snapshot.get("focusedElement"),
        "selectedElements": snapshot.get("selectedElements", []),
        "selectedText": str(snapshot.get("selectedText", "")),
        "documentText": str(snapshot.get("documentText", "")),
        "complete": bool(snapshot.get("complete", True)),
        "truncated": bool(snapshot.get("truncated", False)),
        "identityUnproven": bool(
            snapshot.get("identityUnproven", False)
        ),
        "nodeCount": int(snapshot.get("nodeCount", 0)),
    }
    if text_error is not None:
        result["textError"] = text_error.to_json()
    if screenshot_error is not None:
        result["screenshotError"] = screenshot_error.to_json()
    for name in (
        "path",
        "width",
        "height",
        "originalWidth",
        "originalHeight",
        "scale",
        "mimeType",
        "method",
        "bytes",
    ):
        if name in capture:
            result[name] = capture[name]
    return result


def _uia_available() -> bool:
    return importlib.util.find_spec("comtypes") is not None


def _respond(
    request_id: Any,
    callback: Any,
) -> None:
    try:
        _write(rpc.success_response(request_id, callback()))
    except (TypeError, ValueError) as exc:
        _write(rpc.error_response(request_id, -32602, str(exc)))
    except ComputerError as exc:
        _write(
            rpc.error_response(
                request_id,
                -32000,
                exc.message,
                exc.to_json(),
            )
        )
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _write(
            rpc.error_response(
                request_id,
                -32603,
                f"Internal error: {exc}",
            )
        )


def _run_long(
    request_id: Any,
    method: str,
    params: dict[str, Any],
) -> None:
    def execute() -> dict[str, Any]:
        if method == "window.observe":
            return _observe_window(params)
        if method == "uia.snapshot":
            return snapshot_window(
                window_id=_str_param(
                    params,
                    "window_id",
                    required=True,
                ),
                max_depth=_int_param(params, "max_depth", 8, 1, 50),
                max_nodes=_int_param(params, "max_nodes", 300, 1, 5000),
                max_chars=_int_param(
                    params,
                    "max_chars",
                    30_000,
                    1_000,
                    200_000,
                ),
                query=_str_param(params, "query", ""),
            )
        if method == "uia.find":
            return find_ref(
                window_id=_str_param(
                    params,
                    "window_id",
                    required=True,
                ),
                snapshot_id=_str_param(
                    params,
                    "snapshot_id",
                    required=True,
                ),
                ref=_str_param(params, "ref", required=True),
            )
        if method == "capture.window":
            return capture_window(
                window_id=_str_param(
                    params,
                    "window_id",
                    required=True,
                ),
                max_width=_int_param(
                    params,
                    "max_width",
                    1280,
                    320,
                    3840,
                ),
            )
        overlay = _get_overlay()
        if not overlay.active or _control_stop.is_set():
            raise ComputerError(
                "CONTROL_NOT_ACTIVE",
                "No active computer control session.",
            )
        if method == "input.act":
            window_id = _str_param(
                params,
                "window_id",
                required=True,
            )
            observation_id = _str_param(
                params,
                "observation_id",
                default="",
            )
            snapshot_id = _str_param(
                params,
                "snapshot_id",
                default="",
            )
            capture_id = _str_param(
                params,
                "capture_id",
                default="",
            )
            action = params.get("action")
            actions = params.get("actions")
            if action is not None:
                actions = [action]
            if (
                not isinstance(actions, list)
                or not actions
                or len(actions) != 1
            ):
                raise ValueError(
                    "exactly one action is required per observation"
                )
            if not all(
                isinstance(action, dict) for action in actions
            ):
                raise ValueError("each action must be an object")
            if observation_id:
                observation = _take_observation(observation_id)
                snapshot_id = str(
                    observation.get("snapshotId", "")
                )
                capture_id = str(
                    observation.get("captureId", "")
                )
            return run_actions(
                window_id=window_id,
                snapshot_id=snapshot_id,
                actions=actions,
                should_abort=_control_stop.is_set,
                capture_id=capture_id,
            )
        if method == "app.launch":
            result = launch_app(
                app=_str_param(params, "app", required=True),
                arguments=_str_list_param(params, "arguments"),
                wait_for_window_ms=_int_param(
                    params,
                    "wait_for_window_ms",
                    5000,
                    0,
                    15_000,
                ),
                should_abort=_control_stop.is_set,
            )
            window = result.get("window")
            if isinstance(window, dict) and window.get("id"):
                overlay.update_window(str(window["id"]))
            return result
        raise ValueError(f"Unsupported long method: {method}")

    _respond(request_id, execute)


def _serve(*, parent_pid: int) -> int:
    if parent_pid > 0:
        watch_parent(parent_pid)
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                _write(
                    rpc.error_response(
                        None,
                        -32700,
                        f"Invalid JSON: {exc}",
                    )
                )
                continue
            if not isinstance(request, dict):
                _write(
                    rpc.error_response(
                        None,
                        -32600,
                        "Request must be an object",
                    )
                )
                continue
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if not isinstance(method, str):
                _write(
                    rpc.error_response(
                        request_id,
                        -32600,
                        "Missing method",
                    )
                )
                continue
            if not isinstance(params, dict):
                _write(
                    rpc.error_response(
                        request_id,
                        -32602,
                        "Params must be an object",
                    )
                )
                continue
            if method in _LONG_METHODS:
                _ops.submit(
                    _run_long,
                    request_id,
                    method,
                    params,
                )
                continue
            if method == "initialize":
                _respond(
                    request_id,
                    lambda: {
                        "version": COMPUTER_PROTOCOL_VERSION,
                        "pid": os.getpid(),
                        "capabilities": {
                            "windows": True,
                            "uia": _uia_available(),
                            "input": True,
                            "mouse": True,
                            "clipboard": True,
                            "launch": True,
                            "focus": True,
                            "capture": True,
                            "overlay": True,
                        },
                    },
                )
            elif method == "ping":
                _respond(
                    request_id,
                    lambda: {
                        "pong": True,
                        "nonce": params.get("nonce"),
                    },
                )
            elif method == "windows.list":
                _respond(
                    request_id,
                    lambda: _windows_list(params),
                )
            elif method == "window.focus":
                overlay = _get_overlay()

                def focus() -> dict[str, Any]:
                    if not overlay.active or _control_stop.is_set():
                        raise ComputerError(
                            "CONTROL_NOT_ACTIVE",
                            "No active computer control session.",
                        )
                    result = focus_window(
                        _str_param(
                            params,
                            "window_id",
                            required=True,
                        )
                    )
                    focused = result.get("window")
                    if (
                        isinstance(focused, dict)
                        and focused.get("id")
                    ):
                        overlay.update_window(str(focused["id"]))
                    return result

                _respond(request_id, focus)
            elif method == "control.begin":
                _control_stop.clear()

                def begin() -> dict[str, Any]:
                    try:
                        return _get_overlay().start(
                            on_stop=_on_stop_requested,
                            window_id=_str_param(
                                params,
                                "window_id",
                                default="",
                            ),
                        )
                    except Exception as exc:
                        raise ComputerError(
                            "OVERLAY_FAILED",
                            f"Unable to show the control border: {exc}",
                        ) from exc

                _respond(request_id, begin)
            elif method == "control.end":
                _control_stop.set()
                _get_overlay().stop()
                try:
                    input_api.release_all()
                except OSError:
                    pass
                _respond(
                    request_id,
                    lambda: {"stopped": True},
                )
            elif method == "control.status":
                _respond(
                    request_id,
                    lambda: _get_overlay().status(),
                )
            elif method == "shutdown":
                _control_stop.set()
                overlay = _overlay
                if overlay is not None:
                    overlay.stop()
                try:
                    input_api.release_all()
                except OSError:
                    pass
                _write(rpc.success_response(request_id, {}))
                os._exit(0)
            else:
                _write(
                    rpc.error_response(
                        request_id,
                        -32601,
                        f"Method not found: {method}",
                    )
                )
    finally:
        _control_stop.set()
        overlay = _overlay
        if overlay is not None:
            overlay.stop()
        try:
            input_api.release_all()
        except OSError:
            pass
        os._exit(0)
    return 0


def run_helper(argv: list[str]) -> int:
    args = _parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    set_dpi_awareness()
    if args.self_test:
        payload = list_windows(include_all=False, query="", max_windows=100)
        payload["ok"] = True
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    return _serve(parent_pid=args.parent_pid)


def maybe_run_helper(argv: list[str]) -> bool:
    if "--computer-helper" not in argv:
        return False
    run_helper(argv)
    return True


def main() -> int:
    return run_helper(sys.argv[1:])

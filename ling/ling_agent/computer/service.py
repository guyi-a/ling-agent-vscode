"""Sidecar-side service for Windows computer use."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..config import ComputerUseConfig
from ..effects import ComputerEffect
from .audit import AuditLogger
from .helper_client import HelperClient
from .policy import hard_deny_reason
from .types import ComputerError

EmitControlSession = Callable[
    [dict[str, Any] | None, str],
    Awaitable[None],
]


@dataclass(slots=True)
class ControlSession:
    id: str
    ling_session_id: str
    started_mono: float
    started_ms: int
    last_mono: float
    last_ms: int
    ttl_seconds: float
    idle_seconds: float


class ComputerService:
    """Own the helper process and the desktop control session."""

    def __init__(
        self,
        config: ComputerUseConfig,
        *,
        helper: HelperClient | None = None,
        on_updated: EmitControlSession | None = None,
    ) -> None:
        self._config = config
        self._on_updated = on_updated
        self._helper = helper or HelperClient(
            helper_path=config.helper_path,
            init_timeout=config.helper_init_timeout_seconds,
            call_timeout=config.helper_call_timeout_seconds,
            on_notification=self._on_helper_notification,
            on_lost=self._on_helper_lost,
        )
        self._ling_session_id = ""
        self._session: ControlSession | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._windows: dict[str, dict[str, Any]] = {}
        self._current_window_id = ""
        self._observation_widths: dict[str, int] = {}
        self._pending_grant_mono = 0.0
        self._approval_id = ""
        self._audit = AuditLogger(
            enabled=config.audit_enabled,
            root=config.audit_root or None,
        )

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def bind_session(self, ling_session_id: str) -> None:
        if not isinstance(ling_session_id, str):
            return
        previous = self._ling_session_id
        self._ling_session_id = ling_session_id
        if (
            previous
            and previous != ling_session_id
            and self._session is not None
        ):
            self._schedule_stop("session_changed")

    def status(self) -> dict[str, Any] | None:
        session = self._session
        if session is None:
            return None
        now = time.monotonic()
        if (
            now - session.started_mono >= session.ttl_seconds
            or now - session.last_mono >= session.idle_seconds
        ):
            return None
        return self._view(session)

    def note_approval_granted(
        self,
        effect: object,
        approval_id: str = "",
    ) -> None:
        """Remember an approved takeover before the first action starts.

        Several computer actions may be dispatched in parallel; the first
        one has not created the control session yet when the others pass
        through the approval hook. This short-lived grant keeps them from
        opening duplicate takeover cards.
        """

        if not isinstance(effect, ComputerEffect):
            return
        if effect.classification == "readonly":
            return
        self._pending_grant_mono = time.monotonic()
        if isinstance(approval_id, str) and approval_id:
            self._approval_id = approval_id

    def is_covered(self, effect: object) -> bool:
        if not isinstance(effect, ComputerEffect):
            return False
        if effect.classification == "readonly":
            return False
        now = time.monotonic()
        session = self._session
        if session is not None and (
            now - session.started_mono < session.ttl_seconds
            and now - session.last_mono < session.idle_seconds
        ):
            return True
        pending = self._pending_grant_mono
        return pending > 0 and now - pending < 30.0

    def deny_reason(self, effect: object) -> str | None:
        if not isinstance(effect, ComputerEffect):
            return None
        if effect.classification == "readonly":
            return None
        if effect.operation == "launch":
            return hard_deny_reason(
                effect.app,
                self._config.denied_apps,
            )
        window_id = effect.window_id
        if not window_id:
            return "computer_act requires a window id from computer_list."
        window = self._windows.get(window_id)
        if window is None:
            return (
                "Window is not in the inventory; call computer_list "
                "and computer_observe first."
            )
        if not bool(window.get("controllable", True)):
            return str(
                window.get("denyReason")
                or "Window is blocked by computer-use policy."
            )
        return None

    async def list_windows(
        self,
        *,
        include_all: bool = False,
        query: str = "",
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        result = await self._helper.request(
            "windows.list",
            {
                "include_all": include_all,
                "query": query,
                "max_windows": self._config.list_max_windows,
            },
            timeout=self._config.helper_call_timeout_seconds,
        )
        if (
            not include_all
            and query.strip()
            and not result.get("windows")
        ):
            hidden = await self._helper.request(
                "windows.list",
                {
                    "include_all": True,
                    "query": query,
                    "max_windows": self._config.list_max_windows,
                },
                timeout=self._config.helper_call_timeout_seconds,
            )
            hidden_windows = hidden.get("windows")
            if isinstance(hidden_windows, list) and hidden_windows:
                result = dict(hidden)
                result["hiddenFallback"] = True
        windows = result.get("windows")
        if isinstance(windows, list):
            for window in windows:
                self._remember_window(window)
        return result

    async def observe(
        self,
        *,
        window_id: str,
        include_screenshot: bool = True,
        include_text: bool = True,
        max_width: int = 1280,
        max_depth: int = 8,
        max_nodes: int = 300,
        max_chars: int = 30_000,
        query: str = "",
        call_id: str = "",
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        if not isinstance(window_id, str) or not window_id:
            raise ComputerError(
                "INVALID_ARGS",
                "computer_observe requires a window_id.",
            )
        if not include_screenshot and not include_text:
            raise ComputerError(
                "INVALID_ARGS",
                "include_screenshot or include_text must be true.",
            )
        if not isinstance(max_width, int) or isinstance(
            max_width,
            bool,
        ):
            raise ComputerError(
                "INVALID_ARGS",
                "max_width must be an integer.",
            )
        if not 320 <= max_width <= 3840:
            raise ComputerError(
                "INVALID_ARGS",
                "max_width must be between 320 and 3840.",
            )
        session = self._session
        if include_screenshot:
            self._audit.write(
                "capture.requested",
                sessionId=session.id if session is not None else "",
                approvalId=self._approval_id,
                callId=call_id,
                windowId=window_id,
                maxWidth=max_width,
            )
        try:
            result = await self._helper.request(
                "window.observe",
                {
                    "window_id": window_id,
                    "include_screenshot": include_screenshot,
                    "include_text": include_text,
                    "max_width": max_width,
                    "max_depth": max_depth,
                    "max_nodes": max_nodes,
                    "max_chars": max_chars,
                    "query": query,
                },
                timeout=self._config.helper_call_timeout_seconds,
            )
        except ComputerError as exc:
            if include_screenshot:
                self._audit.write(
                    "capture.failed",
                    sessionId=session.id if session is not None else "",
                    approvalId=self._approval_id,
                    callId=call_id,
                    windowId=window_id,
                    code=exc.code,
                    message=exc.message,
                )
            raise
        self._remember_window(result.get("window"))
        observation_id = result.get("observationId")
        width = result.get("width")
        if (
            isinstance(observation_id, str)
            and observation_id
            and isinstance(width, int)
            and not isinstance(width, bool)
            and width > 0
        ):
            self._observation_widths[observation_id] = width
        if include_screenshot and result.get("captureId"):
            self._audit.write(
                "capture.completed",
                sessionId=session.id if session is not None else "",
                approvalId=self._approval_id,
                callId=call_id,
                windowId=window_id,
                captureId=str(result.get("captureId", "")),
                width=result.get("width"),
                height=result.get("height"),
                bytes=result.get("bytes"),
                method=str(result.get("method", "")),
            )
        elif include_screenshot and isinstance(
            result.get("screenshotError"),
            dict,
        ):
            screenshot_error = result["screenshotError"]
            self._audit.write(
                "capture.failed",
                sessionId=session.id if session is not None else "",
                approvalId=self._approval_id,
                callId=call_id,
                windowId=window_id,
                code=str(screenshot_error.get("code", "")),
                message=str(screenshot_error.get("message", "")),
            )
        return result

    async def find_ref(
        self,
        *,
        window_id: str,
        snapshot_id: str,
        ref: str,
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        return await self._helper.request(
            "uia.find",
            {
                "window_id": window_id,
                "snapshot_id": snapshot_id,
                "ref": ref,
            },
            timeout=self._config.helper_call_timeout_seconds,
        )

    async def screenshot(
        self,
        *,
        window_id: str,
        max_width: int = 1280,
        call_id: str = "",
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        if not isinstance(window_id, str) or not window_id:
            raise ComputerError(
                "INVALID_ARGS",
                "computer_screenshot requires a window_id.",
            )
        if not isinstance(max_width, int) or isinstance(max_width, bool):
            raise ComputerError(
                "INVALID_ARGS",
                "max_width must be an integer.",
            )
        if not 320 <= max_width <= 3840:
            raise ComputerError(
                "INVALID_ARGS",
                "max_width must be between 320 and 3840.",
            )
        session = self._session
        self._audit.write(
            "capture.requested",
            sessionId=session.id if session is not None else "",
            approvalId=self._approval_id,
            callId=call_id,
            windowId=window_id,
            maxWidth=max_width,
        )
        try:
            result = await self._helper.request(
                "capture.window",
                {
                    "window_id": window_id,
                    "max_width": max_width,
                },
                timeout=20.0,
            )
        except ComputerError as exc:
            self._audit.write(
                "capture.failed",
                sessionId=session.id if session is not None else "",
                approvalId=self._approval_id,
                callId=call_id,
                windowId=window_id,
                code=exc.code,
                message=exc.message,
            )
            raise
        window = result.get("window")
        if isinstance(window, dict):
            self._remember_window(window)
        self._audit.write(
            "capture.completed",
            sessionId=session.id if session is not None else "",
            approvalId=self._approval_id,
            callId=call_id,
            windowId=window_id,
            captureId=str(result.get("captureId", "")),
            width=result.get("width"),
            height=result.get("height"),
            bytes=result.get("bytes"),
            method=str(result.get("method", "")),
        )
        return result

    async def act(
        self,
        *,
        window_id: str,
        observation_id: str = "",
        snapshot_id: str = "",
        capture_id: str = "",
        actions: list[dict[str, Any]],
        call_id: str = "",
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        if not isinstance(window_id, str) or not window_id:
            raise ComputerError(
                "INVALID_ARGS",
                "window_id is required.",
            )
        if not isinstance(snapshot_id, str):
            raise ComputerError(
                "INVALID_ARGS",
                "snapshot_id must be a string.",
            )
        if not isinstance(capture_id, str):
            raise ComputerError(
                "INVALID_ARGS",
                "capture_id must be a string.",
            )
        if not isinstance(observation_id, str):
            raise ComputerError(
                "INVALID_ARGS",
                "observation_id must be a string.",
            )
        if not isinstance(actions, list) or len(actions) != 1:
            raise ComputerError(
                "INVALID_ARGS",
                "Exactly one action is required per observation.",
            )
        if not observation_id and not snapshot_id and not capture_id:
            raise ComputerError(
                "INVALID_ARGS",
                "Provide observation_id or legacy snapshot/capture ids.",
            )
        window = self._windows.get(window_id)
        if window is None:
            raise ComputerError(
                "WINDOW_NOT_FOUND",
                "Call computer_list and computer_observe first.",
            )
        await self._ensure_session(window_id)
        session = self._session
        if session is None:
            raise ComputerError(
                "CONTROL_NOT_ACTIVE",
                "Unable to start a control session.",
            )
        session.last_mono = time.monotonic()
        session.last_ms = int(time.time() * 1000)
        self._current_window_id = window_id
        refresh_width = (
            self._observation_widths.pop(observation_id, 0)
            if observation_id
            else 0
        )
        self._audit.write(
            "action.requested",
            sessionId=session.id,
            approvalId=self._approval_id,
            callId=call_id,
            lingSessionId=self._ling_session_id,
            windowId=window_id,
            app=str(window.get("app", "")),
            title=str(window.get("title", ""))[:200],
            observationId=observation_id,
            snapshotId=snapshot_id,
            captureId=capture_id,
            actions=_audit_actions(actions),
        )
        try:
            result = await self._helper.request(
                "input.act",
                {
                    "window_id": window_id,
                    "observation_id": observation_id,
                    "snapshot_id": snapshot_id,
                    "capture_id": capture_id,
                    "actions": actions,
                },
                timeout=self._config.helper_call_timeout_seconds,
            )
        except ComputerError as exc:
            self._audit.write(
                "action.failed",
                sessionId=session.id,
                approvalId=self._approval_id,
                callId=call_id,
                windowId=window_id,
                code=exc.code,
                message=exc.message,
                mayHaveTakenEffect=exc.may_have_taken_effect,
            )
            if _should_stop_control(exc):
                await self.stop(
                    "helper_error",
                    session_id=session.id,
                )
            raise
        result = dict(result)
        result["window"] = {
            "id": window_id,
            "title": str(window.get("title", "")),
            "app": str(window.get("app", "")),
        }
        self._audit.write(
            "action.completed",
            sessionId=session.id,
            approvalId=self._approval_id,
            callId=call_id,
            windowId=window_id,
            delivered=bool(result.get("delivered")),
            mayHaveTakenEffect=bool(
                result.get("mayHaveTakenEffect")
            ),
            actions=_audit_results(result.get("actions")),
        )
        try:
            observation = await self.observe(
                window_id=window_id,
                include_screenshot=True,
                include_text=True,
                max_width=refresh_width or 1280,
                max_nodes=self._config.max_tree_nodes,
                max_chars=self._config.max_tree_chars,
                call_id=f"{call_id}:refresh" if call_id else "",
            )
        except ComputerError as exc:
            result["observationError"] = exc.to_json()
            if _should_stop_control(exc):
                await self.stop(
                    "observation_failed",
                    session_id=session.id,
                )
        else:
            result["observation"] = observation
            refreshed = observation.get("window")
            if isinstance(refreshed, dict):
                self._remember_window(refreshed)
        return result

    async def focus(
        self,
        *,
        window_id: str,
        call_id: str = "",
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        if not isinstance(window_id, str) or not window_id:
            raise ComputerError(
                "INVALID_ARGS",
                "computer_focus requires a window_id.",
            )
        window = self._windows.get(window_id)
        if window is None:
            raise ComputerError(
                "WINDOW_NOT_FOUND",
                "Call computer_list or computer_observe first.",
            )
        await self._ensure_session(window_id)
        session = self._session
        if session is None:
            raise ComputerError(
                "CONTROL_NOT_ACTIVE",
                "Unable to start a control session.",
            )
        session.last_mono = time.monotonic()
        session.last_ms = int(time.time() * 1000)
        self._current_window_id = window_id
        self._audit.write(
            "focus.requested",
            sessionId=session.id,
            approvalId=self._approval_id,
            callId=call_id,
            windowId=window_id,
            app=str(window.get("app", "")),
            title=str(window.get("title", ""))[:200],
        )
        try:
            result = await self._helper.request(
                "window.focus",
                {"window_id": window_id},
                timeout=5.0,
            )
        except ComputerError as exc:
            self._audit.write(
                "focus.failed",
                sessionId=session.id,
                approvalId=self._approval_id,
                callId=call_id,
                windowId=window_id,
                code=exc.code,
                message=exc.message,
            )
            if _should_stop_control(exc):
                await self.stop("helper_error", session_id=session.id)
            raise
        focused = result.get("window")
        if isinstance(focused, dict):
            self._remember_window(focused)
            self._current_window_id = str(focused.get("id", window_id))
            await self._emit("window")
        self._audit.write(
            "focus.completed",
            sessionId=session.id,
            approvalId=self._approval_id,
            callId=call_id,
            windowId=window_id,
            foreground=bool(result.get("foreground")),
            restored=bool(result.get("restored")),
        )
        return result

    async def launch(
        self,
        *,
        app: str,
        arguments: list[str] | None = None,
        wait_for_window_ms: int = 5000,
        call_id: str = "",
    ) -> dict[str, Any]:
        if not self._config.enabled:
            raise ComputerError(
                "COMPUTER_DISABLED",
                "Computer use is disabled.",
            )
        if not isinstance(app, str) or not app.strip():
            raise ComputerError(
                "INVALID_ARGS",
                "computer_launch requires an app name or path.",
            )
        if not isinstance(wait_for_window_ms, int) or isinstance(
            wait_for_window_ms,
            bool,
        ):
            raise ComputerError(
                "INVALID_ARGS",
                "wait_for_window_ms must be an integer.",
            )
        if not 0 <= wait_for_window_ms <= 15_000:
            raise ComputerError(
                "INVALID_ARGS",
                "wait_for_window_ms must be between 0 and 15000.",
            )
        denial = hard_deny_reason(
            app,
            self._config.denied_apps,
        )
        if denial is not None:
            raise ComputerError("APP_BLOCKED", denial)
        await self._ensure_session("")
        session = self._session
        if session is None:
            raise ComputerError(
                "CONTROL_NOT_ACTIVE",
                "Unable to start a control session.",
            )
        session.last_mono = time.monotonic()
        session.last_ms = int(time.time() * 1000)
        self._audit.write(
            "launch.requested",
            sessionId=session.id,
            approvalId=self._approval_id,
            callId=call_id,
            app=app,
            argumentCount=len(arguments or ()),
            waitForWindowMs=wait_for_window_ms,
        )
        try:
            result = await self._helper.request(
                "app.launch",
                {
                    "app": app,
                    "arguments": list(arguments or ()),
                    "wait_for_window_ms": wait_for_window_ms,
                },
                timeout=max(
                    10.0,
                    wait_for_window_ms / 1000 + 5.0,
                ),
            )
        except ComputerError as exc:
            self._audit.write(
                "launch.failed",
                sessionId=session.id,
                approvalId=self._approval_id,
                callId=call_id,
                app=app,
                code=exc.code,
                message=exc.message,
                mayHaveTakenEffect=exc.may_have_taken_effect,
            )
            if _should_stop_control(exc):
                await self.stop(
                    "helper_error",
                    session_id=session.id,
                )
            raise
        window = result.get("window")
        window_id = ""
        if isinstance(window, dict):
            self._remember_window(window)
            window_id = str(window.get("id", ""))
            self._current_window_id = window_id
            await self._emit("window")
        self._audit.write(
            "launch.completed",
            sessionId=session.id,
            approvalId=self._approval_id,
            callId=call_id,
            app=app,
            pid=result.get("pid"),
            windowId=window_id,
            timedOut=bool(result.get("timedOut")),
            reused=bool(result.get("reused")),
        )
        return result

    async def stop(
        self,
        reason: str = "user",
        *,
        session_id: str = "",
    ) -> bool:
        async with self._lifecycle_lock:
            return await self._stop_locked(reason, session_id)

    async def _stop_locked(
        self,
        reason: str,
        session_id: str = "",
    ) -> bool:
        session = self._session
        if session is None:
            return False
        if session_id and session.id != session_id:
            return False
        self._session = None
        self._pending_grant_mono = 0.0
        self._observation_widths.clear()
        task = self._expiry_task
        self._expiry_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        try:
            await self._helper.request(
                "control.end",
                {"reason": reason},
                timeout=0.5,
            )
        except ComputerError:
            pass
        self._audit.write(
            "session.stopped",
            sessionId=session.id,
            approvalId=self._approval_id,
            reason=reason,
            durationMs=int(
                (time.monotonic() - session.started_mono) * 1000
            ),
        )
        self._approval_id = ""
        await self._emit(reason)
        return True

    async def close(self, timeout: float = 0.4) -> None:
        await self.stop("shutdown")
        await self._helper.close(timeout)

    async def _ensure_session(self, window_id: str) -> None:
        async with self._lifecycle_lock:
            session = self._session
            now = time.monotonic()
            if session is not None:
                if (
                    now - session.started_mono < session.ttl_seconds
                    and now - session.last_mono < session.idle_seconds
                ):
                    return
                await self._stop_locked("expired")
            await self._helper.request(
                "control.begin",
                {
                    "window_id": window_id,
                    "ttl_ms": int(
                        self._config.session_ttl_seconds * 1000
                    ),
                    "idle_ms": int(
                        self._config.session_idle_seconds * 1000
                    ),
                },
                timeout=5.0,
            )
            started_mono = time.monotonic()
            started_ms = int(time.time() * 1000)
            self._pending_grant_mono = 0.0
            self._session = ControlSession(
                id=f"control-{uuid.uuid4().hex[:8]}",
                ling_session_id=self._ling_session_id,
                started_mono=started_mono,
                started_ms=started_ms,
                last_mono=started_mono,
                last_ms=started_ms,
                ttl_seconds=self._config.session_ttl_seconds,
                idle_seconds=self._config.session_idle_seconds,
            )
            self._expiry_task = asyncio.create_task(
                self._expire_loop(),
                name="computer-session-expiry",
            )
            window = self._windows.get(window_id, {})
            self._audit.write(
                "session.started",
                sessionId=self._session.id,
                approvalId=self._approval_id,
                lingSessionId=self._ling_session_id,
                windowId=window_id,
                app=str(window.get("app", "")),
                title=str(window.get("title", ""))[:200],
                ttlSeconds=self._config.session_ttl_seconds,
                idleSeconds=self._config.session_idle_seconds,
            )
            await self._emit("started")

    async def _expire_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                session = self._session
                if session is None:
                    return
                now = time.monotonic()
                if (
                    now - session.started_mono >= session.ttl_seconds
                    or now - session.last_mono >= session.idle_seconds
                ):
                    await self.stop(
                        "expired",
                        session_id=session.id,
                    )
                    return
        except asyncio.CancelledError:
            return

    def _schedule_stop(self, reason: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.stop(reason))

    def _on_helper_notification(
        self,
        method: str,
        _params: dict[str, Any],
    ) -> None:
        if method == "control.stopRequested":
            self._schedule_stop("hotkey")

    def _on_helper_lost(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._handle_helper_lost())

    async def _handle_helper_lost(self) -> None:
        async with self._lifecycle_lock:
            session = self._session
            if session is None:
                return
            self._session = None
            self._pending_grant_mono = 0.0
            task = self._expiry_task
            self._expiry_task = None
            if task is not None and task is not asyncio.current_task():
                task.cancel()
            self._audit.write(
                "session.stopped",
                sessionId=session.id,
                approvalId=self._approval_id,
                reason="helper_lost",
                durationMs=int(
                    (time.monotonic() - session.started_mono) * 1000
                ),
            )
            self._approval_id = ""
            await self._emit("helper_lost")

    def _remember_window(self, window: Any) -> None:
        if not isinstance(window, dict):
            return
        window_id = window.get("id")
        if not isinstance(window_id, str) or not window_id:
            return
        entry = dict(window)
        reason = str(entry.get("denyReason") or "") or None
        if reason is None:
            exe = str(entry.get("exeName") or entry.get("app") or "")
            reason = hard_deny_reason(
                exe,
                self._config.denied_apps,
                str(entry.get("aumid") or "") or None,
            )
        entry["controllable"] = reason is None
        entry["denyReason"] = reason
        self._windows[window_id] = entry
        self._current_window_id = window_id

    def _view(self, session: ControlSession) -> dict[str, Any]:
        window = self._windows.get(self._current_window_id, {})
        return {
            "id": session.id,
            "app": str(window.get("app", "")),
            "windowTitle": str(window.get("title", "")),
            "windowId": self._current_window_id,
            "startedAt": session.started_ms,
            "expiresAt": session.started_ms + int(
                session.ttl_seconds * 1000
            ),
            "idleExpiresAt": session.last_ms + int(
                session.idle_seconds * 1000
            ),
        }

    async def _emit(self, reason: str) -> None:
        if self._on_updated is None:
            return
        view = self.status() if self._session is not None else None
        try:
            await self._on_updated(view, reason)
        except Exception:
            pass


def _should_stop_control(exc: ComputerError) -> bool:
    """Stop takeover only when helper state may be unsafe."""

    return (
        exc.code.startswith("HELPER_")
        or exc.code == "CONTROL_NOT_ACTIVE"
        or exc.may_have_taken_effect
    )


def _audit_key(action: dict[str, Any]) -> dict[str, Any]:
    key = action.get("key")
    if not isinstance(key, str) or not key:
        return {}
    modifiers = action.get("modifiers")
    has_modifiers = isinstance(modifiers, list) and bool(modifiers)
    named = key.casefold() in {
        "backspace",
        "delete",
        "del",
        "down",
        "end",
        "enter",
        "escape",
        "esc",
        "home",
        "insert",
        "left",
        "pagedown",
        "pageup",
        "return",
        "right",
        "space",
        "tab",
        "up",
    }
    function_key = (
        key.casefold().startswith("f")
        and key[1:].isdigit()
    )
    if "+" in key or has_modifiers or named or function_key:
        return {"key": key}
    return {
        "keyLength": len(key),
        "keyRedacted": True,
    }


def _audit_actions(
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Describe actions without recording text, values, or keys."""

    result: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        entry: dict[str, Any] = {"type": action.get("type")}
        for name in (
            "ref",
            "target_ref",
            "direction",
            "amount",
            "x",
            "y",
            "to_x",
            "to_y",
            "button",
            "clicks",
        ):
            if name in action:
                entry[name] = action[name]
        if "modifiers" in action:
            entry["modifiers"] = action["modifiers"]
        if action.get("type") == "press_key":
            entry.update(_audit_key(action))
        if "text" in action:
            entry["textLength"] = len(str(action["text"]))
        if "value" in action:
            entry["valueLength"] = len(str(action["value"]))
        result.append(entry)
    return result


def _audit_results(
    steps: Any,
) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    result: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        result.append(
            {
                name: step.get(name)
                for name in (
                    "type",
                    "ref",
                    "targetRef",
                    "x",
                    "y",
                    "screenX",
                    "screenY",
                    "toX",
                    "toY",
                    "ok",
                    "mechanism",
                    "verified",
                    "clipboardRestored",
                    "clipboardReplaced",
                    "state",
                    "selected",
                )
                if name in step
            }
        )
    return result


__all__ = ["ComputerService", "ControlSession"]

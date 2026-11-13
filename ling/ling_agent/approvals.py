"""Approval contracts for mutating tools and commands."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from ling_core import AgentFatalError, HookCallback, PreToolUse, PreToolUseDecision

from .effects import (
    ApprovalMode,
    BrowserEffect,
    Effect,
    FilesystemWriteEffect,
    McpAdminEffect,
    McpEffect,
    ProcessExecEffect,
    approval_reason,
    derive_effect,
    effect_digest,
    plan_mode_denial,
)
from .workspace import Workspace


class ApprovalKind(str, Enum):
    WRITE = "write"
    EDIT = "edit"
    COMMAND = "command"
    BROWSER = "browser"
    UNKNOWN = "unknown"
    MCP = "mcp"


class ApprovalDecision(str, Enum):
    ALLOW_ONCE = "allow_once"
    DENY = "deny"


@dataclass(slots=True, frozen=True)
class ApprovalRequest:
    kind: ApprovalKind
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    workspace_root: Path
    call_id: str = ""
    approval_id: str = ""
    run_id: str = ""
    turn_id: str = ""
    effect: Effect | None = None
    effect_digest: str = ""
    created_at: int = 0
    cancel_event: asyncio.Event | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __deepcopy__(self, memo: dict[int, object]) -> "ApprovalRequest":
        copied = replace(
            self,
            arguments=deepcopy(self.arguments, memo),
            effect=deepcopy(self.effect, memo),
            cancel_event=None,
        )
        memo[id(self)] = copied
        return copied


class ApprovalHandler(Protocol):
    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        """Resolve a pending harness approval."""


@dataclass(slots=True)
class _PendingApproval:
    request: ApprovalRequest
    future: asyncio.Future[ApprovalDecision]


class ApprovalManager:
    """Track pending approvals and bind decisions to exact effects."""

    def __init__(self) -> None:
        self._by_id: dict[str, _PendingApproval] = {}
        self._by_call: dict[str, str] = {}

    def open(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        effect: Effect,
        reason: str,
        workspace_root: Path,
        run_id: str = "",
        turn_id: str = "",
        cancel_event: asyncio.Event | None = None,
    ) -> ApprovalRequest:
        digest = effect_digest(effect, _CallView(tool_name, arguments))
        existing_id = self._by_call.get(call_id)
        if existing_id is not None:
            existing = self._by_id.get(existing_id)
            if existing is not None and not existing.future.done():
                if existing.request.effect_digest != digest:
                    raise RuntimeError("Tool call changed while awaiting approval")
                return existing.request

        approval_id = f"approval-{uuid.uuid4()}"
        request = ApprovalRequest(
            kind=_kind_for_effect(effect),
            tool_name=tool_name,
            arguments=arguments,
            reason=reason,
            workspace_root=workspace_root,
            call_id=call_id,
            approval_id=approval_id,
            run_id=run_id,
            turn_id=turn_id,
            effect=effect,
            effect_digest=digest,
            created_at=int(time.time() * 1000),
            cancel_event=cancel_event,
        )
        pending = _PendingApproval(
            request=request,
            future=asyncio.get_running_loop().create_future(),
        )
        self._by_id[approval_id] = pending
        self._by_call[call_id] = approval_id
        return request

    async def wait(
        self,
        approval_id: str,
        *,
        cancel_event: asyncio.Event | None,
        timeout_seconds: float,
    ) -> ApprovalDecision:
        pending = self._by_id.get(approval_id)
        if pending is None:
            raise KeyError(f"Unknown approval: {approval_id}")

        wait_task = asyncio.ensure_future(asyncio.shield(pending.future))
        timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
        cancel_task = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        tasks = {wait_task, timeout_task}
        if cancel_task is not None:
            tasks.add(cancel_task)
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if wait_task in done:
            decision = await wait_task
        else:
            decision = ApprovalDecision.DENY
            self.resolve(
                approval_id,
                pending.request.effect_digest,
                ApprovalDecision.DENY,
            )

        wait_task.cancel()
        timeout_task.cancel()
        if cancel_task is not None:
            cancel_task.cancel()
        for task in (wait_task, timeout_task, cancel_task):
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task
        return decision

    def resolve(
        self,
        approval_id: str,
        digest: str,
        decision: ApprovalDecision,
    ) -> bool:
        pending = self._by_id.get(approval_id)
        if pending is None:
            return False
        if pending.request.effect_digest != digest or pending.future.done():
            return False
        pending.future.set_result(decision)
        self._remove(pending.request)
        return True

    def fail(self, approval_id: str, error: BaseException) -> None:
        pending = self._by_id.get(approval_id)
        if pending is None or pending.future.done():
            return
        pending.future.set_exception(error)
        self._remove(pending.request)

    def cancel_all(self, reason: str = "Approval cancelled") -> None:
        for pending in list(self._by_id.values()):
            self.resolve(
                pending.request.approval_id,
                pending.request.effect_digest,
                ApprovalDecision.DENY,
            )

    def pending(self) -> list[ApprovalRequest]:
        return [
            pending.request
            for pending in self._by_id.values()
            if not pending.future.done()
        ]

    def _remove(self, request: ApprovalRequest) -> None:
        self._by_id.pop(request.approval_id, None)
        self._by_call.pop(request.call_id, None)


class _CallView:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments


def _kind_for_effect(effect: Effect) -> ApprovalKind:
    if isinstance(effect, FilesystemWriteEffect):
        return ApprovalKind.WRITE if effect.operation == "write" else ApprovalKind.EDIT
    if isinstance(effect, ProcessExecEffect):
        return ApprovalKind.COMMAND
    if isinstance(effect, BrowserEffect):
        return ApprovalKind.BROWSER
    if isinstance(effect, (McpEffect, McpAdminEffect)):
        return ApprovalKind.MCP
    return ApprovalKind.UNKNOWN


def _deny(reason: str) -> PreToolUseDecision:
    return PreToolUseDecision(allowed=False, reason=reason)


def create_approval_hook(
    handler: ApprovalHandler | None,
    workspace: Workspace | str | Path,
    *,
    mode: ApprovalMode | Callable[[], ApprovalMode] = ApprovalMode.MANUAL,
    manager: ApprovalManager | None = None,
    timeout_seconds: float = 120.0,
    effect_resolver: Callable[[ToolCall], Effect | None] | None = None,
) -> HookCallback:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    resolved_workspace = (
        workspace if isinstance(workspace, Workspace) else Workspace(workspace)
    )
    approval_manager = manager or ApprovalManager()
    mode_provider = mode if callable(mode) else lambda: mode

    async def approval_hook(event: PreToolUse) -> PreToolUseDecision | None:
        if (
            event.context.cancel_event is not None
            and event.context.cancel_event.is_set()
        ):
            return _deny("Approval cancelled")

        effect = (
            effect_resolver(event.call)
            if effect_resolver is not None
            else None
        ) or derive_effect(event.call, resolved_workspace)
        mode_now = mode_provider()
        if mode_now is ApprovalMode.PLAN:
            denial = plan_mode_denial(effect)
            if denial is not None:
                return _deny(denial)
        reason = approval_reason(effect, mode_now, event.call)
        if reason is None:
            return None
        if handler is None:
            return _deny("Approval required but no handler is configured")

        request = approval_manager.open(
            call_id=event.call.id,
            tool_name=event.call.name,
            arguments=event.call.arguments,
            effect=effect,
            reason=reason,
            workspace_root=resolved_workspace.root,
            cancel_event=event.context.cancel_event,
        )
        denial_reason: str | None = None

        async def present() -> None:
            nonlocal denial_reason
            try:
                result = handler.request(request)
                if inspect.isawaitable(result):
                    result = await result
                decision = (
                    result
                    if isinstance(result, ApprovalDecision)
                    else ApprovalDecision.DENY
                )
                if (
                    request.cancel_event is not None
                    and request.cancel_event.is_set()
                ):
                    decision = ApprovalDecision.DENY
                approval_manager.resolve(
                    request.approval_id,
                    request.effect_digest,
                    decision,
                )
            except AgentFatalError as exc:
                approval_manager.fail(request.approval_id, exc)
            except BaseException as exc:
                denial_reason = f"Approval handler failed: {exc}"
                approval_manager.resolve(
                    request.approval_id,
                    request.effect_digest,
                    ApprovalDecision.DENY,
                )

        present_task = asyncio.create_task(present())
        try:
            result = await approval_manager.wait(
                request.approval_id,
                cancel_event=event.context.cancel_event,
                timeout_seconds=timeout_seconds,
            )
        except AgentFatalError:
            raise
        finally:
            if not present_task.done():
                present_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await present_task

        if result is ApprovalDecision.ALLOW_ONCE:
            return PreToolUseDecision(allowed=True)
        return _deny(denial_reason or "Permission denied")

    setattr(approval_hook, "approval_manager", approval_manager)
    return approval_hook


__all__ = [
    "ApprovalDecision",
    "ApprovalHandler",
    "ApprovalKind",
    "ApprovalManager",
    "ApprovalMode",
    "ApprovalRequest",
    "create_approval_hook",
]

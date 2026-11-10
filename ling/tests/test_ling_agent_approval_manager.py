"""Tests for approval digest, pending lifecycle, and hook policy."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.types import ToolCall
from ling_agent import (
    ApprovalDecision,
    ApprovalManager,
    ApprovalMode,
    ApprovalRequest,
    FilesystemWriteEffect,
    ProcessExecEffect,
    Workspace,
    create_approval_hook,
    effect_digest,
)
from ling_core import PreToolUse, ToolContext


class DecisionHandler:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decision


def _call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def test_effect_digest_binds_tool_and_arguments() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        call = _call(
            "write_file",
            {"path": "a.txt", "content": "hello"},
        )
        effect = FilesystemWriteEffect(operation="write", path=root / "a.txt")
        first = effect_digest(effect, call)
        second = effect_digest(
            effect,
            _call("write_file", {"content": "hello", "path": "a.txt"}),
        )
        changed = effect_digest(
            effect,
            _call("write_file", {"path": "a.txt", "content": "changed"}),
        )

        assert first == second
        assert first != changed


async def _manager_contracts(root: Path) -> None:
    manager = ApprovalManager()
    call = _call("write_file", {"path": "a.txt"})
    effect = FilesystemWriteEffect(operation="write", path=root / "a.txt")
    request = manager.open(
        call_id=call.id,
        tool_name=call.name,
        arguments=call.arguments,
        effect=effect,
        reason="write",
        workspace_root=root,
    )
    assert request.approval_id
    assert manager.pending() == [request]
    assert not manager.resolve(
        request.approval_id,
        "wrong",
        ApprovalDecision.ALLOW_ONCE,
    )
    assert manager.resolve(
        request.approval_id,
        request.effect_digest,
        ApprovalDecision.ALLOW_ONCE,
    )
    assert not manager.resolve(
        request.approval_id,
        request.effect_digest,
        ApprovalDecision.DENY,
    )
    assert manager.pending() == []

    cancel = asyncio.Event()
    cancelled_request = manager.open(
        call_id="call-2",
        tool_name=call.name,
        arguments={"path": "b.txt"},
        effect=FilesystemWriteEffect(operation="write", path=root / "b.txt"),
        reason="write",
        workspace_root=root,
    )
    cancel.set()
    decision = await manager.wait(
        cancelled_request.approval_id,
        cancel_event=cancel,
        timeout_seconds=1,
    )
    assert decision is ApprovalDecision.DENY


def test_approval_manager_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_manager_contracts(Path(temp)))


async def _hook_modes(root: Path) -> None:
    workspace = Workspace(root)
    write_event = PreToolUse(
        call=_call(
            "write_file",
            {"path": "a.txt", "content": "hello"},
        ),
        context=ToolContext(step=1),
    )
    command_event = PreToolUse(
        call=_call("run_command", {"command": "npm test"}),
        context=ToolContext(step=1),
    )
    destructive_event = PreToolUse(
        call=_call("run_command", {"command": "git reset --hard"}),
        context=ToolContext(step=1),
    )
    sensitive_event = PreToolUse(
        call=_call(
            "write_file",
            {"path": ".env", "content": "API_KEY=secret"},
        ),
        context=ToolContext(step=1),
    )

    manual_handler = DecisionHandler(ApprovalDecision.ALLOW_ONCE)
    manual = create_approval_hook(
        manual_handler,
        workspace,
        mode=ApprovalMode.MANUAL,
    )
    manual_write = await manual(write_event)
    assert manual_write is not None and manual_write.allowed
    assert len(manual_handler.requests) == 1

    auto_handler = DecisionHandler(ApprovalDecision.ALLOW_ONCE)
    auto = create_approval_hook(
        auto_handler,
        workspace,
        mode=ApprovalMode.AUTO,
    )
    assert await auto(write_event) is None
    assert await auto(command_event) is None
    assert len(auto_handler.requests) == 0

    destructive = await auto(destructive_event)
    assert destructive is not None and destructive.allowed
    sensitive = await auto(sensitive_event)
    assert sensitive is not None and sensitive.allowed
    assert len(auto_handler.requests) == 2


def test_approval_hook_modes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_hook_modes(Path(temp)))


if __name__ == "__main__":
    test_effect_digest_binds_tool_and_arguments()
    test_approval_manager_lifecycle()
    test_approval_hook_modes()
    print("all ling_agent approval manager tests passed")

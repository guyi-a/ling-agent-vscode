"""Tests for computer-use approval and desktop grants."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_agent import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalMode,
    ApprovalRequest,
    create_approval_hook,
)
from ling_ai.types import ToolCall
from ling_core import PreToolUse, ToolContext


class StaticApprovalHandler:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    async def request(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        self.requests.append(request)
        return self.decision


class GatedApprovalHandler:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []
        self.gate = asyncio.Event()

    async def request(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        self.requests.append(request)
        await self.gate.wait()
        return ApprovalDecision.ALLOW_ONCE


def _pre_tool(
    name: str,
    arguments: dict,
    call_id: str = "call-1",
) -> PreToolUse:
    return PreToolUse(
        call=ToolCall(
            id=call_id,
            name=name,
            arguments=arguments,
        ),
        context=ToolContext(step=1),
    )


def _act_call(call_id: str = "call-1") -> PreToolUse:
    return _pre_tool(
        "computer_act",
        {
            "window_id": "w-1",
            "snapshot_id": "snap-1",
            "actions": [{"type": "click", "ref": "@snap-1:e1"}],
        },
        call_id=call_id,
    )


def test_act_prompts_once_without_grant() -> None:
    with tempfile.TemporaryDirectory() as temp:
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)

        async def scenario() -> None:
            hook = create_approval_hook(
                handler,
                Path(temp),
                mode=ApprovalMode.AUTO,
                timeout_seconds=0.5,
                deny_checker=lambda effect, call: None,
                grant_checker=lambda effect, call: False,
            )
            decision = await hook(_act_call())
            assert decision is not None and decision.allowed

        asyncio.run(scenario())
        assert len(handler.requests) == 1
        request = handler.requests[0]
        assert request.kind is ApprovalKind.COMPUTER
        assert "computer" in request.reason.lower()


def test_active_grant_skips_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        handler = StaticApprovalHandler(ApprovalDecision.DENY)

        async def scenario() -> None:
            hook = create_approval_hook(
                handler,
                Path(temp),
                mode=ApprovalMode.MANUAL,
                timeout_seconds=0.5,
                deny_checker=lambda effect, call: None,
                grant_checker=lambda effect, call: True,
            )
            decision = await hook(_act_call())
            assert decision is not None and decision.allowed

        asyncio.run(scenario())
        assert handler.requests == []


def test_hard_denial_wins_over_grant() -> None:
    with tempfile.TemporaryDirectory() as temp:
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)

        async def scenario() -> None:
            hook = create_approval_hook(
                handler,
                Path(temp),
                mode=ApprovalMode.AUTO,
                timeout_seconds=0.5,
                deny_checker=lambda effect, call: (
                    "terminal windows are blocked"
                ),
                grant_checker=lambda effect, call: True,
            )
            decision = await hook(_act_call())
            assert decision is not None
            assert decision.allowed is False
            assert "terminal" in (decision.reason or "")

        asyncio.run(scenario())
        assert handler.requests == []


def test_plan_mode_blocks_act_before_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)

        async def scenario() -> None:
            hook = create_approval_hook(
                handler,
                Path(temp),
                mode=ApprovalMode.PLAN,
                timeout_seconds=0.5,
                deny_checker=lambda effect, call: None,
                grant_checker=lambda effect, call: True,
            )
            decision = await hook(_act_call())
            assert decision is not None
            assert decision.allowed is False
            assert "Plan mode" in (decision.reason or "")

        asyncio.run(scenario())
        assert handler.requests == []


def test_parallel_takeover_approvals_share_one_card() -> None:
    with tempfile.TemporaryDirectory() as temp:
        async def scenario() -> None:
            handler = GatedApprovalHandler()
            hook = create_approval_hook(
                handler,
                Path(temp),
                mode=ApprovalMode.MANUAL,
                timeout_seconds=2.0,
                deny_checker=lambda effect, call: None,
                grant_checker=lambda effect, call: False,
            )
            first = asyncio.create_task(
                hook(_act_call("call-1"))
            )
            second = asyncio.create_task(
                hook(_act_call("call-2"))
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert len(handler.requests) == 1
            handler.gate.set()
            decisions = await asyncio.gather(first, second)
            assert all(
                decision is not None and decision.allowed
                for decision in decisions
            )
            assert len(handler.requests) == 1

        asyncio.run(scenario())


def test_grant_callback_records_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)
        granted: list[tuple[object, str]] = []

        async def scenario() -> None:
            hook = create_approval_hook(
                handler,
                Path(temp),
                mode=ApprovalMode.MANUAL,
                timeout_seconds=0.5,
                deny_checker=lambda effect, call: None,
                grant_checker=lambda effect, call: False,
                on_grant=lambda effect, approval_id: granted.append(
                    (effect, approval_id)
                ),
            )
            decision = await hook(_act_call())
            assert decision is not None and decision.allowed

        asyncio.run(scenario())
        assert len(handler.requests) == 1
        assert len(granted) == 1
        assert granted[0][1].startswith("approval-")


def test_readonly_computer_tools_never_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        async def scenario() -> None:
            hook = create_approval_hook(
                None,
                Path(temp),
                mode=ApprovalMode.MANUAL,
                timeout_seconds=0.1,
            )
            assert await hook(
                _pre_tool("computer_list", {})
            ) is None
            assert await hook(
                _pre_tool(
                    "computer_observe",
                    {"window_id": "w-1"},
                )
            ) is None
            assert await hook(
                _pre_tool("computer_stop", {})
            ) is None

        asyncio.run(scenario())


if __name__ == "__main__":
    test_act_prompts_once_without_grant()
    test_active_grant_skips_prompt()
    test_hard_denial_wins_over_grant()
    test_plan_mode_blocks_act_before_approval()
    test_parallel_takeover_approvals_share_one_card()
    test_grant_callback_records_approval()
    test_readonly_computer_tools_never_prompt()
    print("computer approval tests passed")

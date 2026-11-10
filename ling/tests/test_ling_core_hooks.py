"""Tests for ling_core hook registration and dispatch."""

from __future__ import annotations

import asyncio

from ling_ai.types import (
    AssistantMessage,
    Context,
    Tool,
    ToolCall,
    Usage,
    UserMessage,
)
from ling_core.hooks import (
    HookRegistry,
    PostToolUse,
    PostToolUseDecision,
    PreToolUse,
    PreToolUseDecision,
    Stop,
    StopDecision,
    UserPromptDecision,
    UserPromptSubmit,
)
from ling_core.tools import ToolContext


def _tool_call() -> ToolCall:
    return ToolCall(id="call_1", name="echo", arguments={"value": "hello"})


def _assistant() -> AssistantMessage:
    return AssistantMessage(
        content=[],
        provider="test",
        model="test",
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )


async def _test_user_prompt_hooks() -> None:
    registry = HookRegistry()
    seen: list[str] = []

    def replace(event: UserPromptSubmit) -> UserPromptDecision:
        seen.append("replace")
        return UserPromptDecision(prompt="updated")

    async def block(event: UserPromptSubmit) -> UserPromptDecision:
        await asyncio.sleep(0)
        seen.append("block")
        return UserPromptDecision(block_reason="denied")

    def skipped(event: UserPromptSubmit) -> UserPromptDecision:
        seen.append("skipped")
        return UserPromptDecision()

    registry.register(UserPromptSubmit, replace)
    registry.register(UserPromptSubmit, block)
    registry.register(UserPromptSubmit, skipped)
    decisions = await registry.dispatch(
        UserPromptSubmit(prompt="hello", context=Context())
    )

    assert seen == ["replace", "block"]
    assert len(decisions) == 2
    assert decisions[-1].block_reason == "denied"


async def _test_tool_and_stop_hooks() -> None:
    registry = HookRegistry()
    pre_seen: list[str] = []
    post_seen: list[str] = []
    stop_seen: list[str] = []
    context = ToolContext(step=1)

    registry.register(
        PreToolUse,
        lambda event: (
            pre_seen.append("allow"),
            PreToolUseDecision(allowed=True),
        )[1],
    )
    registry.register(
        PreToolUse,
        lambda event: (
            pre_seen.append("deny"),
            PreToolUseDecision(allowed=False, reason="blocked"),
        )[1],
    )
    registry.register(
        PreToolUse,
        lambda event: pre_seen.append("skipped"),
    )

    registry.register(
        PostToolUse,
        lambda event: (
            post_seen.append("first"),
            PostToolUseDecision(output="first"),
        )[1],
    )
    registry.register(
        PostToolUse,
        lambda event: (
            post_seen.append("second"),
            PostToolUseDecision(output="second"),
        )[1],
    )

    registry.register(
        Stop,
        lambda event: (
            stop_seen.append("continue"),
            StopDecision(continue_prompt="again"),
        )[1],
    )
    registry.register(
        Stop,
        lambda event: stop_seen.append("skipped"),
    )

    pre = await registry.dispatch(PreToolUse(call=_tool_call(), context=context))
    post = await registry.dispatch(
        PostToolUse(call=_tool_call(), output="raw", context=context)
    )
    stop = await registry.dispatch(
        Stop(message=_assistant(), context=Context(), steps=1)
    )

    assert pre_seen == ["allow", "deny"]
    assert [decision.allowed for decision in pre] == [True, False]
    assert post_seen == ["first", "second"]
    assert [decision.output for decision in post] == ["first", "second"]
    assert stop_seen == ["continue"]
    assert stop[0].continue_prompt == "again"


def test_hook_dispatch() -> None:
    asyncio.run(_test_user_prompt_hooks())
    asyncio.run(_test_tool_and_stop_hooks())


if __name__ == "__main__":
    test_hook_dispatch()
    print("all ling_core hook tests passed")

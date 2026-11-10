"""Lifecycle hooks and ordered dispatch for ling_core."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, ClassVar, TypeAlias

from ling_ai.types import AssistantMessage, Context, ToolCall, UserMessage

from .tools import ToolContext, ToolOutput


class HookEvent:
    stage: ClassVar[str]


@dataclass(slots=True)
class UserPromptSubmit(HookEvent):
    prompt: str | UserMessage
    context: Context
    stage: ClassVar[str] = "user_prompt_submit"


@dataclass(slots=True)
class PreToolUse(HookEvent):
    call: ToolCall
    context: ToolContext
    stage: ClassVar[str] = "pre_tool_use"


@dataclass(slots=True)
class PostToolUse(HookEvent):
    call: ToolCall
    output: ToolOutput
    context: ToolContext
    is_error: bool = False
    stage: ClassVar[str] = "post_tool_use"


@dataclass(slots=True)
class Stop(HookEvent):
    message: AssistantMessage
    context: Context
    steps: int
    stage: ClassVar[str] = "stop"


@dataclass(slots=True)
class UserPromptDecision:
    prompt: str | UserMessage | None = None
    block_reason: str | None = None


@dataclass(slots=True)
class PreToolUseDecision:
    allowed: bool
    reason: str | None = None


@dataclass(slots=True)
class PostToolUseDecision:
    output: ToolOutput


@dataclass(slots=True)
class StopDecision:
    continue_prompt: str | UserMessage | None


HookDecision: TypeAlias = (
    UserPromptDecision
    | PreToolUseDecision
    | PostToolUseDecision
    | StopDecision
)
HookCallback: TypeAlias = (
    Callable[[HookEvent], HookDecision | None | Awaitable[HookDecision | None]]
)

_EVENT_DECISION: dict[type[HookEvent], type[HookDecision]] = {
    UserPromptSubmit: UserPromptDecision,
    PreToolUse: PreToolUseDecision,
    PostToolUse: PostToolUseDecision,
    Stop: StopDecision,
}


class HookRegistry:
    """Per-Agent hook registry with registration-order dispatch."""

    def __init__(self) -> None:
        self._handlers: dict[type[HookEvent], list[HookCallback]] = {}

    def register(
        self,
        event_type: type[HookEvent],
        callback: HookCallback,
    ) -> None:
        if event_type not in _EVENT_DECISION:
            raise TypeError(f"Unsupported hook event: {event_type!r}")
        self._handlers.setdefault(event_type, []).append(callback)

    def handlers(self, event_type: type[HookEvent]) -> tuple[HookCallback, ...]:
        return tuple(self._handlers.get(event_type, ()))

    async def dispatch(self, event: HookEvent) -> tuple[HookDecision, ...]:
        event_type = type(event)
        expected = _EVENT_DECISION.get(event_type)
        if expected is None:
            raise TypeError(f"Unsupported hook event: {event_type!r}")

        decisions: list[HookDecision] = []
        for callback in self._handlers.get(event_type, ()):
            decision = callback(event)
            if inspect.isawaitable(decision):
                decision = await decision
            if decision is None:
                continue
            if not isinstance(decision, expected):
                raise TypeError(
                    f"{event_type.__name__} hook returned "
                    f"{type(decision).__name__}, expected {expected.__name__}"
                )

            decisions.append(decision)
            if isinstance(event, PostToolUse) and isinstance(
                decision, PostToolUseDecision
            ):
                # Later PostToolUse hooks observe the replaced output.
                event.output = decision.output
            if isinstance(decision, UserPromptDecision) and decision.block_reason:
                break
            if isinstance(decision, PreToolUseDecision) and not decision.allowed:
                break
            if (
                isinstance(decision, StopDecision)
                and decision.continue_prompt is not None
            ):
                break

        return tuple(decisions)


__all__ = [
    "HookCallback",
    "HookDecision",
    "HookEvent",
    "HookRegistry",
    "PostToolUse",
    "PostToolUseDecision",
    "PreToolUse",
    "PreToolUseDecision",
    "Stop",
    "StopDecision",
    "UserPromptDecision",
    "UserPromptSubmit",
]

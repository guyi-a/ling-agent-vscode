"""Agent-level control errors."""

from __future__ import annotations

from ling_ai.types import AssistantMessage


class AgentError(RuntimeError):
    """Base class for failures controlled by the agent runtime."""


class AgentFatalError(AgentError):
    """Failure that must abort the run instead of becoming a tool result."""


class AgentInputBlocked(AgentError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AgentHookError(AgentError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage} hook failed: {message}")
        self.stage = stage
        self.message = message


class AgentLoopLimitError(AgentError):
    def __init__(self, max_steps: int, last_message: AssistantMessage) -> None:
        super().__init__(f"Agent exceeded the maximum of {max_steps} steps")
        self.max_steps = max_steps
        self.last_message = last_message


__all__ = [
    "AgentError",
    "AgentFatalError",
    "AgentHookError",
    "AgentInputBlocked",
    "AgentLoopLimitError",
]

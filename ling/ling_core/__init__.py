"""Agent orchestration layer built on ling_ai."""

from .agent import Agent, EventHandler
from .errors import (
    AgentError,
    AgentFatalError,
    AgentHookError,
    AgentInputBlocked,
    AgentLoopLimitError,
)
from .hooks import (
    HookCallback,
    HookDecision,
    HookEvent,
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
from .tools import (
    AgentTool,
    ToolContext,
    ToolExecution,
    ToolHandler,
    ToolOutput,
    ToolRegistry,
)

__all__ = [
    "Agent",
    "AgentError",
    "AgentFatalError",
    "AgentHookError",
    "AgentInputBlocked",
    "AgentLoopLimitError",
    "AgentTool",
    "EventHandler",
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
    "ToolContext",
    "ToolExecution",
    "ToolHandler",
    "ToolOutput",
    "ToolRegistry",
    "UserPromptDecision",
    "UserPromptSubmit",
]

"""Tool registration and execution for ling_core."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeAlias

from ling_ai.types import (
    TextContent,
    Tool,
    ToolCall,
    ToolResultContent,
    ToolResultMessage,
)

ToolOutput: TypeAlias = str | list[ToolResultContent] | None


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class ToolContext:
    step: int
    cancel_event: asyncio.Event | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTool:
    definition: Tool
    handler: ToolHandler


@dataclass(slots=True)
class ToolExecution:
    output: ToolOutput
    is_error: bool = False


ToolHandler: TypeAlias = Callable[
    [ToolCall, "ToolContext"],
    ToolOutput | ToolExecution | Awaitable[ToolOutput | ToolExecution],
]


def _output_content(output: ToolOutput) -> list[ToolResultContent]:
    if output is None:
        return [TextContent("(no output)")]
    if isinstance(output, str):
        return [TextContent(output)]
    return output


class ToolRegistry:
    """Name-based tool registry owned by one Agent instance."""

    def __init__(self, tools: tuple[AgentTool, ...] | list[AgentTool] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Duplicate tool name: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def definitions(self) -> list[Tool]:
        return [tool.definition for tool in self._tools.values()]

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
    ) -> ToolResultMessage:
        execution = await self.execute_raw(call, context)
        return self.to_result(call, execution)

    async def execute_raw(
        self,
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        tool = self.get(call.name)
        if tool is None:
            return ToolExecution(
                output=f"Error: unknown tool: {call.name}",
                is_error=True,
            )

        try:
            output = tool.handler(call, context)
            if inspect.isawaitable(output):
                output = await output
            if isinstance(output, ToolExecution):
                return output
            return ToolExecution(output=output)
        except Exception as exc:
            return ToolExecution(
                output=f"Error: {type(exc).__name__}: {exc}",
                is_error=True,
            )

    @staticmethod
    def to_result(call: ToolCall, execution: ToolExecution) -> ToolResultMessage:
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=_output_content(execution.output),
            is_error=execution.is_error,
            timestamp=_now_ms(),
        )


__all__ = [
    "AgentTool",
    "ToolContext",
    "ToolExecution",
    "ToolHandler",
    "ToolOutput",
    "ToolRegistry",
]

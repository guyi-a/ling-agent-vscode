"""Read-only explore subagent."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ling_ai.events import DoneEvent
from ling_ai.stream import StreamFunction, StreamOptions
from ling_ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ThinkingContent,
)

from ling_core import Agent, AgentTool, HookRegistry, ToolExecution

from .config import HarnessConfig
from .tools import create_file_tools, create_search_tools
from .workspace import Workspace

if TYPE_CHECKING:
    from ling_retrieval.embedding import Embedder

EXPLORE_TOOL_NAMES = frozenset(
    {
        "read_file",
        "list_files",
        "glob",
        "grep",
        "search_codebase",
    }
)

TRACE_OUTPUT_CHARS = 4_000
MAX_TRACE_ENTRIES = 200

TraceSink = Callable[[str, dict[str, Any]], None | Awaitable[None]]


class SubagentError(RuntimeError):
    """A subagent could not finish its task."""


@dataclass(slots=True, frozen=True)
class SubagentResult:
    summary: str
    trace: tuple[dict[str, Any], ...]


def build_explore_prompt(
    workspace_root: str,
    description: str,
) -> str:
    return "\n".join(
        [
            "You are an explore subagent for Ling.",
            "Your job is read-only codebase research: search, read, and report.",
            "You cannot edit files, run commands, or ask the user questions.",
            "Use search_codebase for concepts, grep for exact identifiers,",
            "glob or list_files to discover files, and read_file to verify.",
            "When finished, reply with a concise summary that answers the task",
            "and cites the relevant workspace-relative paths and line numbers.",
            f"Workspace root: {workspace_root}",
            f"Task: {description}",
        ]
    )


def _assistant_trace(
    message: AssistantMessage,
    entry_id: str,
) -> dict[str, Any] | None:
    text = "".join(
        block.text
        for block in message.content
        if isinstance(block, TextContent)
    ).strip()
    thinking = "".join(
        block.thinking
        for block in message.content
        if isinstance(block, ThinkingContent)
    ).strip()
    if not text and not thinking:
        return None
    return {
        "kind": "assistant",
        "id": entry_id,
        "text": text,
        **({"thinking": thinking} if thinking else {}),
    }


def _tool_output(value: object) -> tuple[str, bool]:
    if isinstance(value, ToolExecution):
        output = value.output
        is_error = value.is_error
    else:
        output = value
        is_error = False
    if output is None:
        return "", is_error
    if isinstance(output, str):
        return output, is_error
    if isinstance(output, list):
        return (
            "\n".join(
                block.text
                for block in output
                if isinstance(block, TextContent)
            ),
            is_error,
        )
    return str(output), is_error


class SubagentRunner:
    """Run foreground subagents in an isolated, read-only context."""

    def __init__(
        self,
        *,
        model: Model,
        stream_fn: StreamFunction,
        workspace: Workspace,
        config: HarnessConfig,
        embedder: "Embedder | None" = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.model = model
        self.stream_fn = stream_fn
        self.workspace = workspace
        self.config = config
        self.embedder = embedder
        self.trace_sink = trace_sink

    def _tools(self) -> list[AgentTool]:
        tools = [
            *create_file_tools(self.workspace),
            *create_search_tools(
                self.workspace,
                embedder=self.embedder,
            ),
        ]
        return [
            tool
            for tool in tools
            if tool.definition.name in EXPLORE_TOOL_NAMES
        ]

    async def spawn_explore(
        self,
        description: str,
        prompt: str,
        *,
        call_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> SubagentResult:
        if not description.strip():
            raise ValueError("description must be a non-empty string")
        if not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        trace: list[dict[str, Any]] = []
        trace_id = call_id or "subagent"

        async def emit(entry: dict[str, Any]) -> None:
            if len(trace) >= MAX_TRACE_ENTRIES:
                return
            trace.append(entry)
            if self.trace_sink is None:
                return
            maybe = self.trace_sink(trace_id, entry)
            if inspect.isawaitable(maybe):
                await maybe

        def traced(tool: AgentTool) -> AgentTool:
            original = tool.handler

            async def handler(call, context):
                await emit(
                    {
                        "kind": "tool_call",
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                )
                result = original(call, context)
                if inspect.isawaitable(result):
                    result = await result
                output, is_error = _tool_output(result)
                if len(output) > TRACE_OUTPUT_CHARS:
                    output = (
                        output[:TRACE_OUTPUT_CHARS] + "\n…[truncated]"
                    )
                await emit(
                    {
                        "kind": "tool_result",
                        "id": call.id,
                        "name": call.name,
                        "output": output,
                        "isError": is_error,
                    }
                )
                return result

            return AgentTool(definition=tool.definition, handler=handler)

        context = Context(
            system_prompt=build_explore_prompt(
                str(self.workspace.root),
                description.strip(),
            )
        )
        assistant_index = 0

        async def on_event(event: object) -> None:
            nonlocal assistant_index
            if not isinstance(event, DoneEvent):
                return
            assistant_index += 1
            entry = _assistant_trace(
                event.message,
                f"assistant-{assistant_index}",
            )
            if entry is not None:
                await emit(entry)

        agent = Agent(
            model=self.model,
            stream_fn=self.stream_fn,
            context=context,
            tools=[traced(tool) for tool in self._tools()],
            hooks=HookRegistry(),
            options=StreamOptions(cancel_event=cancel_event),
            max_steps=self.config.subagent_max_steps,
            max_parallel_tools=self.config.agent_max_parallel_tools,
        )
        try:
            result = await agent.run(prompt.strip(), on_event=on_event)
        except Exception as exc:
            message = f"The explore subagent failed: {exc}"
            await emit({"kind": "error", "message": message})
            raise SubagentError(message) from exc

        summary = ""
        failure: str | None = None
        if result.stop_reason == "aborted":
            failure = "The explore subagent was interrupted."
        elif result.stop_reason == "error":
            failure = result.error_message or "The explore subagent failed."
        else:
            summary = "\n".join(
                block.text
                for block in result.content
                if isinstance(block, TextContent)
            ).strip()
            if not summary:
                failure = (
                    "The explore subagent completed without a summary."
                )

        if failure is not None:
            await emit({"kind": "error", "message": failure})
            raise SubagentError(failure)

        limit = self.config.subagent_max_summary_chars
        if len(summary) > limit:
            summary = summary[:limit] + "\n…[truncated]"
        return SubagentResult(summary=summary, trace=tuple(trace))


__all__ = [
    "EXPLORE_TOOL_NAMES",
    "MAX_TRACE_ENTRIES",
    "TRACE_OUTPUT_CHARS",
    "SubagentError",
    "SubagentResult",
    "SubagentRunner",
    "TraceSink",
    "build_explore_prompt",
]

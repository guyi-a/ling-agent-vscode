"""Core agent loop."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Awaitable, Callable, Iterable, TypeAlias

from ling_ai.events import AssistantMessageEvent
from ling_ai.stream import StreamFunction, StreamOptions
from ling_ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from .errors import (
    AgentFatalError,
    AgentHookError,
    AgentInputBlocked,
    AgentLoopLimitError,
)
from .hooks import (
    HookRegistry,
    PostToolUse,
    PreToolUse,
    Stop,
    UserPromptSubmit,
)
from .tools import AgentTool, ToolContext, ToolExecution, ToolRegistry

EventHandler: TypeAlias = (
    Callable[[AssistantMessageEvent], None | Awaitable[None]]
)
PrepareContext: TypeAlias = Callable[[Context], Context | Awaitable[Context]]


def _now_ms() -> int:
    return int(time.time() * 1000)


class Agent:
    """Run a model and execute requested tools until it stops."""

    def __init__(
        self,
        *,
        model: Model,
        stream_fn: StreamFunction,
        context: Context | None = None,
        tools: Iterable[AgentTool] = (),
        hooks: HookRegistry | None = None,
        options: StreamOptions | None = None,
        max_steps: int = 50,
        max_parallel_tools: int = 1,
        prepare_context: PrepareContext | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_parallel_tools < 1:
            raise ValueError("max_parallel_tools must be at least 1")

        self.model = model
        self.stream_fn = stream_fn
        self.context = context or Context()
        self.tools = ToolRegistry(list(tools))
        self.context.tools = self.tools.definitions()
        self.hooks = hooks or HookRegistry()
        self.options = options or StreamOptions()
        self.max_steps = max_steps
        self.max_parallel_tools = max_parallel_tools
        self.prepare_context = prepare_context

    async def run(
        self,
        prompt: str | UserMessage | None = None,
        *,
        on_event: EventHandler | None = None,
    ) -> AssistantMessage:
        if self._is_cancelled():
            return self._append_aborted_message()

        if prompt is not None:
            self.context.messages.append(await self._resolve_prompt(prompt))

        last_message: AssistantMessage | None = None
        for step in range(1, self.max_steps + 1):
            if self._is_cancelled():
                return self._append_aborted_message()

            model_context = self.context
            if self.prepare_context is not None:
                prepared = self.prepare_context(self.context)
                if inspect.isawaitable(prepared):
                    prepared = await prepared
                model_context = prepared

            stream = self.stream_fn(self.model, model_context, self.options)
            async for event in stream:
                if on_event is not None:
                    callback_result = on_event(event)
                    if inspect.isawaitable(callback_result):
                        await callback_result

            last_message = await stream.result()
            self.context.messages.append(last_message)
            if last_message.stop_reason in ("error", "aborted"):
                return last_message

            tool_calls = [
                block for block in last_message.content if isinstance(block, ToolCall)
            ]
            if not tool_calls:
                if self._is_cancelled():
                    return self._mark_aborted(last_message)
                try:
                    decisions = await self.hooks.dispatch(
                        Stop(
                            message=last_message,
                            context=self.context,
                            steps=step,
                        )
                    )
                except AgentFatalError:
                    raise
                except Exception as exc:
                    raise AgentHookError("Stop", str(exc)) from exc

                if self._is_cancelled():
                    return self._mark_aborted(last_message)

                continuation = next(
                    (
                        decision.continue_prompt
                        for decision in decisions
                        if decision.continue_prompt is not None
                    ),
                    None,
                )
                if continuation is None:
                    return last_message
                if isinstance(continuation, str):
                    continuation = UserMessage(
                        content=continuation,
                        timestamp=_now_ms(),
                    )
                self.context.messages.append(continuation)
                continue

            if self.max_parallel_tools <= 1 or len(tool_calls) <= 1:
                for index, call in enumerate(tool_calls):
                    if self._is_cancelled():
                        self._append_cancelled_tool_results(
                            tool_calls[index:]
                        )
                        return self._mark_aborted(last_message)
                    self.context.messages.append(
                        await self._execute_tool(call, step)
                    )
            else:
                self.context.messages.extend(
                    await self._execute_tool_calls_parallel(
                        tool_calls,
                        step,
                    )
                )

            if self._is_cancelled():
                return self._mark_aborted(last_message)

        assert last_message is not None
        raise AgentLoopLimitError(self.max_steps, last_message)

    async def _execute_tool_calls_parallel(
        self,
        calls: list[ToolCall],
        step: int,
    ) -> list[ToolResultMessage]:
        semaphore = asyncio.Semaphore(self.max_parallel_tools)
        cancel_event = self.options.cancel_event

        async def run(
            index: int,
            call: ToolCall,
        ) -> tuple[int, ToolResultMessage]:
            if cancel_event is not None and cancel_event.is_set():
                return index, self._error_tool_result(
                    call,
                    "Error: cancelled before tool execution",
                )
            async with semaphore:
                if cancel_event is not None and cancel_event.is_set():
                    return index, self._error_tool_result(
                        call,
                        "Error: cancelled before tool execution",
                    )
                return index, await self._execute_tool(call, step)

        tasks = [
            asyncio.create_task(run(index, call))
            for index, call in enumerate(calls)
        ]
        try:
            pairs = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        pairs.sort(key=lambda pair: pair[0])
        return [result for _, result in pairs]

    def _is_cancelled(self) -> bool:
        cancel_event = self.options.cancel_event
        return cancel_event is not None and cancel_event.is_set()

    def _append_aborted_message(self) -> AssistantMessage:
        message = AssistantMessage(
            content=[],
            provider=self.model.provider,
            model=self.model.id,
            api=self.model.api,
            usage=Usage(),
            stop_reason="aborted",
            error_message="Request aborted",
            timestamp=_now_ms(),
        )
        self.context.messages.append(message)
        return message

    @staticmethod
    def _mark_aborted(message: AssistantMessage) -> AssistantMessage:
        message.stop_reason = "aborted"
        message.error_message = "Request aborted"
        message.timestamp = _now_ms()
        return message

    def _append_cancelled_tool_results(self, calls: Iterable[ToolCall]) -> None:
        for call in calls:
            self.context.messages.append(
                self._error_tool_result(
                    call,
                    "Error: cancelled before tool execution",
                )
            )

    async def _resolve_prompt(
        self,
        prompt: str | UserMessage,
    ) -> UserMessage:
        resolved: str | UserMessage = prompt
        try:
            decisions = await self.hooks.dispatch(
                UserPromptSubmit(prompt=prompt, context=self.context)
            )
        except AgentFatalError:
            raise
        except Exception as exc:
            raise AgentInputBlocked(
                f"UserPromptSubmit hook failed: {exc}"
            ) from exc

        for decision in decisions:
            if decision.block_reason:
                raise AgentInputBlocked(decision.block_reason)
            if decision.prompt is not None:
                resolved = decision.prompt

        if isinstance(resolved, str):
            return UserMessage(content=resolved, timestamp=_now_ms())
        return resolved

    async def _execute_tool(
        self,
        call: ToolCall,
        step: int,
    ) -> ToolResultMessage:
        context = ToolContext(
            step=step,
            cancel_event=self.options.cancel_event,
        )
        try:
            pre_decisions = await self.hooks.dispatch(
                PreToolUse(call=call, context=context)
            )
        except AgentFatalError:
            raise
        except Exception as exc:
            return self._error_tool_result(
                call,
                f"Error: PreToolUse hook failed: {exc}",
            )

        for decision in pre_decisions:
            if not decision.allowed:
                reason = decision.reason or "Tool execution denied"
                return self._error_tool_result(call, f"Error: {reason}")

        execution = await self.tools.execute_raw(call, context)
        try:
            post_decisions = await self.hooks.dispatch(
                PostToolUse(
                    call=call,
                    output=execution.output,
                    context=context,
                    is_error=execution.is_error,
                )
            )
        except AgentFatalError:
            raise
        except Exception as exc:
            execution = ToolExecution(
                output=f"Error: PostToolUse hook failed: {exc}",
                is_error=True,
            )
        else:
            for decision in post_decisions:
                execution.output = decision.output

        return self.tools.to_result(call, execution)

    @staticmethod
    def _error_tool_result(call: ToolCall, message: str) -> ToolResultMessage:
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[TextContent(message)],
            is_error=True,
            timestamp=_now_ms(),
        )


__all__ = ["Agent", "EventHandler", "PrepareContext"]

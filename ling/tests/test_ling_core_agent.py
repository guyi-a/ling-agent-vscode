"""Tests for the base ling_core agent loop."""

from __future__ import annotations

import asyncio

from ling_ai.events import (
    AssistantMessageEvent,
    DoneEvent,
    ErrorEvent,
    StartEvent,
)
from ling_ai.stream import AssistantMessageEventStream, StreamOptions
from ling_ai.types import (
    AssistantContent,
    AssistantMessage,
    Context,
    Message,
    Model,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ling_core.agent import Agent
from ling_core.errors import (
    AgentFatalError,
    AgentHookError,
    AgentInputBlocked,
    AgentLoopLimitError,
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
from ling_core.tools import AgentTool, ToolExecution

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _assistant(
    content: list[AssistantContent],
    *,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
    )


class ScriptedStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)
        self.contexts: list[list[Message]] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        self.contexts.append(list(context.messages))
        message = self.messages.pop(0)
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=message))
        if message.stop_reason in ("error", "aborted"):
            stream.push(ErrorEvent(reason=message.stop_reason, error=message))
        else:
            stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


def test_agent_returns_without_tools() -> None:
    streams = ScriptedStreams([_assistant([TextContent("done")])])
    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        context=Context(system_prompt="You are helpful."),
    )
    seen: list[str] = []

    result = asyncio.run(
        agent.run("hello", on_event=lambda event: seen.append(event.type))
    )

    assert result.content == [TextContent("done")]
    assert seen == ["start", "done"]
    assert agent.context.system_prompt == "You are helpful."
    assert isinstance(agent.context.messages[0], UserMessage)
    assert agent.context.messages[1] is result


def test_agent_executes_tools_in_order() -> None:
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(id="call_1", name="echo", arguments={"value": "hello"}),
                    ToolCall(id="call_2", name="upper", arguments={"value": "done"}),
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("hello DONE")]),
        ]
    )
    executed: list[tuple[str, str]] = []

    async def echo(call: ToolCall, context: object) -> str:
        await asyncio.sleep(0)
        value = call.arguments["value"]
        executed.append(("echo", value))
        return value

    def upper(call: ToolCall, context: object) -> str:
        value = call.arguments["value"]
        executed.append(("upper", value))
        return value.upper()

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        tools=[
            AgentTool(
                definition=Tool(name="echo", description="Echo."),
                handler=echo,
            ),
            AgentTool(
                definition=Tool(name="upper", description="Uppercase."),
                handler=upper,
            ),
        ],
    )

    result = asyncio.run(agent.run("use tools"))

    assert result.content == [TextContent("hello DONE")]
    assert executed == [("echo", "hello"), ("upper", "done")]
    assert len(streams.contexts) == 2
    results = [
        message
        for message in streams.contexts[1]
        if isinstance(message, ToolResultMessage)
    ]
    assert [message.content[0].text for message in results] == ["hello", "DONE"]


def test_agent_executes_parallel_tools_and_preserves_order() -> None:
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(id="call_1", name="slow", arguments={}),
                    ToolCall(id="call_2", name="fast", arguments={}),
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("done")]),
        ]
    )
    started: list[str] = []
    finished: list[str] = []
    release = asyncio.Event()

    async def slow(call: ToolCall, context: object) -> str:
        started.append(call.id)
        await release.wait()
        finished.append(call.id)
        return "slow"

    async def fast(call: ToolCall, context: object) -> str:
        started.append(call.id)
        finished.append(call.id)
        release.set()
        return "fast"

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        tools=[
            AgentTool(
                definition=Tool(name="slow", description="Slow."),
                handler=slow,
            ),
            AgentTool(
                definition=Tool(name="fast", description="Fast."),
                handler=fast,
            ),
        ],
        max_parallel_tools=2,
    )

    result = asyncio.run(agent.run("go"))

    assert result.content == [TextContent("done")]
    assert started == ["call_1", "call_2"]
    assert finished == ["call_2", "call_1"]
    results = [
        message
        for message in streams.contexts[1]
        if isinstance(message, ToolResultMessage)
    ]
    assert [message.content[0].text for message in results] == [
        "slow",
        "fast",
    ]


def test_agent_bounds_parallel_tools() -> None:
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(
                        id=f"call_{index}",
                        name="work",
                        arguments={},
                    )
                    for index in range(1, 5)
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("done")]),
        ]
    )
    started: list[str] = []
    release = asyncio.Event()
    running = 0
    peak = 0

    async def work(call: ToolCall, context: object) -> str:
        nonlocal running, peak
        started.append(call.id)
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        return call.id

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        tools=[
            AgentTool(
                definition=Tool(name="work", description="Work."),
                handler=work,
            )
        ],
        max_parallel_tools=2,
    )

    async def run_agent() -> AssistantMessage:
        task = asyncio.create_task(agent.run("go"))
        for _ in range(100):
            if len(started) >= 2:
                break
            await asyncio.sleep(0)
        assert len(started) == 2
        release.set()
        return await task

    result = asyncio.run(run_agent())

    assert result.content == [TextContent("done")]
    assert peak == 2
    assert started == ["call_1", "call_2", "call_3", "call_4"]


def test_cancel_prevents_queued_parallel_tools() -> None:
    cancel = asyncio.Event()
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(
                        id=f"call_{index}",
                        name="work",
                        arguments={},
                    )
                    for index in range(1, 5)
                ],
                stop_reason="tool_use",
            )
        ]
    )
    started: list[str] = []
    release = asyncio.Event()

    async def work(call: ToolCall, context: object) -> str:
        started.append(call.id)
        await release.wait()
        return call.id

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        options=StreamOptions(cancel_event=cancel),
        tools=[
            AgentTool(
                definition=Tool(name="work", description="Work."),
                handler=work,
            )
        ],
        max_parallel_tools=2,
    )

    async def run_agent() -> AssistantMessage:
        task = asyncio.create_task(agent.run("go"))
        for _ in range(100):
            if len(started) >= 2:
                break
            await asyncio.sleep(0)
        assert len(started) == 2
        cancel.set()
        release.set()
        return await task

    result = asyncio.run(run_agent())

    assert result.stop_reason == "aborted"
    assert started == ["call_1", "call_2"]
    results = [
        message
        for message in agent.context.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert len(results) == 4
    assert [not message.is_error for message in results] == [
        True,
        True,
        False,
        False,
    ]
    assert "cancelled before tool execution" in results[2].content[0].text


def test_agent_prepare_context_is_model_view_only() -> None:
    streams = ScriptedStreams([_assistant([TextContent("done")])])
    prepared = Context(
        messages=[UserMessage(content="summary", timestamp=1)],
        system_prompt="system",
    )
    seen: list[str] = []

    def prepare(context: Context) -> Context:
        seen.extend(
            message.content
            for message in context.messages
            if isinstance(message, UserMessage)
        )
        return prepared

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        context=Context(system_prompt="system"),
        prepare_context=prepare,
    )

    result = asyncio.run(agent.run("hello"))

    assert result.content == [TextContent("done")]
    assert seen == ["hello"]
    assert len(streams.contexts[0]) == 1
    assert streams.contexts[0][0].content == "summary"
    assert isinstance(agent.context.messages[0], UserMessage)
    assert agent.context.messages[0].content == "hello"
    assert agent.context.messages[-1] is result


def test_agent_recovers_from_tool_errors() -> None:
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(id="call_1", name="missing", arguments={}),
                    ToolCall(id="call_2", name="fail", arguments={}),
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("recovered")]),
        ]
    )

    def fail(call: ToolCall, context: object) -> str:
        raise ValueError("boom")

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        tools=[
            AgentTool(
                definition=Tool(name="fail", description="Fail."),
                handler=fail,
            )
        ],
    )

    result = asyncio.run(agent.run("try tools"))

    assert result.content == [TextContent("recovered")]
    results = [
        message
        for message in agent.context.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert [message.is_error for message in results] == [True, True]
    assert "unknown tool" in results[0].content[0].text
    assert "ValueError: boom" in results[1].content[0].text


def test_agent_returns_model_errors_without_running_tools() -> None:
    error = _assistant(
        [ToolCall(id="call_1", name="echo", arguments={"value": "x"})],
        stop_reason="error",
    )
    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams([error]),
        tools=[
            AgentTool(
                definition=Tool(name="echo", description="Echo."),
                handler=lambda call, context: "unexpected",
            )
        ],
    )

    result = asyncio.run(agent.run("fail"))

    assert result is error
    assert not any(
        isinstance(message, ToolResultMessage) for message in agent.context.messages
    )


def test_agent_loop_limit() -> None:
    repeated = _assistant(
        [ToolCall(id="call_1", name="echo", arguments={"value": "again"})],
        stop_reason="tool_use",
    )
    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams([repeated, repeated]),
        tools=[
            AgentTool(
                definition=Tool(name="echo", description="Echo."),
                handler=lambda call, context: call.arguments["value"],
            )
        ],
        max_steps=2,
    )

    try:
        asyncio.run(agent.run("loop"))
    except AgentLoopLimitError as exc:
        assert exc.max_steps == 2
        assert exc.last_message.stop_reason == "tool_use"
    else:
        raise AssertionError("Expected AgentLoopLimitError")


def test_agent_accepts_async_event_handler() -> None:
    seen: list[AssistantMessageEvent] = []

    async def on_event(event: AssistantMessageEvent) -> None:
        await asyncio.sleep(0)
        seen.append(event)

    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams([_assistant([TextContent("ok")])]),
    )
    asyncio.run(agent.run("hello", on_event=on_event))

    assert [event.type for event in seen] == ["start", "done"]


def test_user_prompt_hook_can_replace_and_block() -> None:
    streams = ScriptedStreams([_assistant([TextContent("ok")])])
    hooks = HookRegistry()
    hooks.register(
        UserPromptSubmit,
        lambda event: UserPromptDecision(prompt="replaced"),
    )
    agent = Agent(model=MODEL, stream_fn=streams, hooks=hooks)

    asyncio.run(agent.run("original"))

    first_message = streams.contexts[0][0]
    assert isinstance(first_message, UserMessage)
    assert first_message.content == "replaced"

    blocked_streams = ScriptedStreams([_assistant([TextContent("unused")])])
    blocked_hooks = HookRegistry()
    blocked_hooks.register(
        UserPromptSubmit,
        lambda event: UserPromptDecision(block_reason="input denied"),
    )
    blocked_agent = Agent(
        model=MODEL,
        stream_fn=blocked_streams,
        hooks=blocked_hooks,
    )

    try:
        asyncio.run(blocked_agent.run("blocked"))
    except AgentInputBlocked as exc:
        assert exc.reason == "input denied"
    else:
        raise AssertionError("Expected AgentInputBlocked")
    assert blocked_streams.contexts == []


def test_pre_and_post_tool_hooks() -> None:
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(id="call_1", name="read", arguments={}),
                    ToolCall(id="call_2", name="delete", arguments={}),
                ],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("handled")]),
        ]
    )
    hooks = HookRegistry()
    executed: list[str] = []

    def pre_tool(event: PreToolUse) -> PreToolUseDecision:
        if event.call.name == "delete":
            return PreToolUseDecision(allowed=False, reason="permission denied")
        return PreToolUseDecision(allowed=True)

    hooks.register(PreToolUse, pre_tool)
    hooks.register(
        PostToolUse,
        lambda event: PostToolUseDecision(output="[redacted]"),
    )
    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        hooks=hooks,
        tools=[
            AgentTool(
                definition=Tool(name="read", description="Read."),
                handler=lambda call, context: (
                    executed.append("read") or "secret"
                ),
            ),
            AgentTool(
                definition=Tool(name="delete", description="Delete."),
                handler=lambda call, context: (
                    executed.append("delete") or "deleted"
                ),
            ),
        ],
    )

    asyncio.run(agent.run("act"))

    assert executed == ["read"]
    results = [
        message
        for message in agent.context.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert [message.is_error for message in results] == [False, True]
    assert results[0].content == [TextContent("[redacted]")]
    assert results[1].content == [TextContent("Error: permission denied")]


def test_stop_hook_can_continue_once() -> None:
    streams = ScriptedStreams(
        [
            _assistant([TextContent("not done")]),
            _assistant([TextContent("done")]),
        ]
    )
    hooks = HookRegistry()
    calls = 0

    def stop_hook(event: Stop) -> StopDecision | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return StopDecision(continue_prompt="finish the task")
        return None

    hooks.register(Stop, stop_hook)
    agent = Agent(model=MODEL, stream_fn=streams, hooks=hooks)

    result = asyncio.run(agent.run("work"))

    assert result.content == [TextContent("done")]
    assert calls == 2
    assert isinstance(agent.context.messages[-2], UserMessage)
    assert agent.context.messages[-2].content == "finish the task"


def test_hook_failures_use_stage_specific_policy() -> None:
    prompt_streams = ScriptedStreams([_assistant([TextContent("unused")])])
    prompt_hooks = HookRegistry()

    def fail_prompt(event: UserPromptSubmit) -> UserPromptDecision:
        raise ValueError("prompt failed")

    prompt_hooks.register(UserPromptSubmit, fail_prompt)
    prompt_agent = Agent(
        model=MODEL,
        stream_fn=prompt_streams,
        hooks=prompt_hooks,
    )
    try:
        asyncio.run(prompt_agent.run("hello"))
    except AgentInputBlocked as exc:
        assert "prompt failed" in exc.reason
    else:
        raise AssertionError("Expected AgentInputBlocked")
    assert prompt_streams.contexts == []

    tool_streams = ScriptedStreams(
        [
            _assistant(
                [ToolCall(id="call_1", name="echo", arguments={})],
                stop_reason="tool_use",
            ),
            _assistant([TextContent("recovered")]),
        ]
    )
    tool_hooks = HookRegistry()

    def fail_pre(event: PreToolUse) -> PreToolUseDecision:
        raise ValueError("pre failed")

    tool_hooks.register(PreToolUse, fail_pre)
    tool_agent = Agent(
        model=MODEL,
        stream_fn=tool_streams,
        hooks=tool_hooks,
        tools=[
            AgentTool(
                definition=Tool(name="echo", description="Echo."),
                handler=lambda call, context: "unused",
            )
        ],
    )
    result = asyncio.run(tool_agent.run("tool"))
    assert result.content == [TextContent("recovered")]
    tool_result = next(
        message
        for message in tool_agent.context.messages
        if isinstance(message, ToolResultMessage)
    )
    assert tool_result.is_error
    assert "PreToolUse hook failed" in tool_result.content[0].text

    stop_hooks = HookRegistry()

    def fail_stop(event: Stop) -> StopDecision:
        raise ValueError("stop failed")

    stop_hooks.register(Stop, fail_stop)
    stop_agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams([_assistant([TextContent("done")])]),
        hooks=stop_hooks,
    )
    try:
        asyncio.run(stop_agent.run("hello"))
    except AgentHookError as exc:
        assert exc.stage == "Stop"
    else:
        raise AssertionError("Expected AgentHookError")


def test_fatal_hook_error_aborts_run() -> None:
    hooks = HookRegistry()

    def fatal_pre(event: PreToolUse) -> PreToolUseDecision:
        raise AgentFatalError("persistence unavailable")

    hooks.register(PreToolUse, fatal_pre)
    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams(
            [
                _assistant(
                    [ToolCall(id="call_1", name="echo", arguments={})],
                    stop_reason="tool_use",
                )
            ]
        ),
        hooks=hooks,
        tools=[
            AgentTool(
                definition=Tool(name="echo", description="Echo."),
                handler=lambda call, context: "unused",
            )
        ],
    )

    try:
        asyncio.run(agent.run("fatal"))
    except AgentFatalError as exc:
        assert "persistence unavailable" in str(exc)
    else:
        raise AssertionError("Expected AgentFatalError")
    assert not any(
        isinstance(message, ToolResultMessage) for message in agent.context.messages
    )


def test_post_tool_hook_receives_error_state() -> None:
    hooks = HookRegistry()
    seen: list[bool] = []

    def post_tool(event: PostToolUse) -> None:
        seen.append(event.is_error)

    hooks.register(PostToolUse, post_tool)
    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams(
            [
                _assistant(
                    [ToolCall(id="call_1", name="fail", arguments={})],
                    stop_reason="tool_use",
                ),
                _assistant([TextContent("recovered")]),
            ]
        ),
        hooks=hooks,
        tools=[
            AgentTool(
                definition=Tool(name="fail", description="Fail."),
                handler=lambda call, context: ToolExecution(
                    output="structured failure",
                    is_error=True,
                ),
            )
        ],
    )

    asyncio.run(agent.run("tool"))

    assert seen == [True]


def test_post_tool_hook_receives_details() -> None:
    hooks = HookRegistry()
    seen: list[object] = []

    def post_tool(event: PostToolUse) -> None:
        seen.append(event.details)

    hooks.register(PostToolUse, post_tool)
    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams(
            [
                _assistant(
                    [ToolCall(id="call_1", name="plan", arguments={})],
                    stop_reason="tool_use",
                ),
                _assistant([TextContent("done")]),
            ]
        ),
        hooks=hooks,
        tools=[
            AgentTool(
                definition=Tool(name="plan", description="Plan."),
                handler=lambda call, context: ToolExecution(
                    output="ok",
                    details={"kind": "plan"},
                ),
            )
        ],
    )

    asyncio.run(agent.run("tool"))

    assert seen == [{"kind": "plan"}]


def test_cancel_before_model_returns_aborted_message() -> None:
    cancel = asyncio.Event()
    cancel.set()
    streams = ScriptedStreams([])
    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        options=StreamOptions(cancel_event=cancel),
    )

    result = asyncio.run(agent.run("hello"))

    assert result.stop_reason == "aborted"
    assert streams.contexts == []
    assert agent.context.messages == [result]


def test_cancel_can_stop_remaining_tool_calls() -> None:
    cancel = asyncio.Event()
    streams = ScriptedStreams(
        [
            _assistant(
                [
                    ToolCall(id="call_1", name="first", arguments={}),
                    ToolCall(id="call_2", name="second", arguments={}),
                ],
                stop_reason="tool_use",
            )
        ]
    )
    executed: list[str] = []

    def first(call: ToolCall, context: object) -> str:
        executed.append("first")
        cancel.set()
        return "first result"

    def second(call: ToolCall, context: object) -> str:
        executed.append("second")
        return "second result"

    agent = Agent(
        model=MODEL,
        stream_fn=streams,
        options=StreamOptions(cancel_event=cancel),
        tools=[
            AgentTool(
                definition=Tool(name="first", description="First."),
                handler=first,
            ),
            AgentTool(
                definition=Tool(name="second", description="Second."),
                handler=second,
            ),
        ],
    )

    result = asyncio.run(agent.run("run tools"))

    assert executed == ["first"]
    assert result.stop_reason == "aborted"
    results = [
        message
        for message in agent.context.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert len(results) == 2
    assert results[0].content == [TextContent("first result")]
    assert not results[0].is_error
    assert results[1].is_error
    assert "cancelled before tool execution" in results[1].content[0].text
    assert len(streams.contexts) == 1


def test_cancel_before_stop_hook_skips_hook() -> None:
    cancel = asyncio.Event()
    hooks = HookRegistry()
    stop_calls = 0

    def stop_hook(event: Stop) -> None:
        nonlocal stop_calls
        stop_calls += 1

    hooks.register(Stop, stop_hook)
    agent = Agent(
        model=MODEL,
        stream_fn=ScriptedStreams([_assistant([TextContent("done")])]),
        hooks=hooks,
        options=StreamOptions(cancel_event=cancel),
    )

    async def on_event(event: AssistantMessageEvent) -> None:
        if event.type == "done":
            cancel.set()

    result = asyncio.run(agent.run("hello", on_event=on_event))

    assert result.stop_reason == "aborted"
    assert stop_calls == 0


if __name__ == "__main__":
    test_agent_returns_without_tools()
    test_agent_executes_tools_in_order()
    test_agent_recovers_from_tool_errors()
    test_agent_returns_model_errors_without_running_tools()
    test_agent_loop_limit()
    test_agent_accepts_async_event_handler()
    test_user_prompt_hook_can_replace_and_block()
    test_pre_and_post_tool_hooks()
    test_stop_hook_can_continue_once()
    test_hook_failures_use_stage_specific_policy()
    test_fatal_hook_error_aborts_run()
    test_post_tool_hook_receives_error_state()
    test_cancel_before_model_returns_aborted_message()
    test_cancel_can_stop_remaining_tool_calls()
    test_cancel_before_stop_hook_skips_hook()
    print("all ling_core agent tests passed")

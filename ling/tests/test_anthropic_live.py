"""Opt-in live smoke tests against an Anthropic-compatible endpoint."""

from __future__ import annotations

import asyncio
import os

from ling_ai.adapters.anthropic_messages import (
    create_anthropic_messages_stream,
)
from ling_ai.stream import StreamOptions
from ling_ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _enabled() -> bool:
    return os.environ.get("LING_LIVE_ANTHROPIC") == "1"


def _api_key() -> str:
    return (
        os.environ.get("LING_API_KEY", "").strip()
        or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    )


def _model() -> Model:
    return Model(
        id=os.environ.get(
            "LING_LIVE_ANTHROPIC_MODEL",
            "deepseek-flash",
        ),
        provider="deepseek",
        api="anthropic-messages",
        base_url=os.environ.get(
            "LING_LIVE_ANTHROPIC_BASE_URL",
            "https://api.deepseek.com/anthropic",
        ),
    )


async def _run(
    prompt: str,
    *,
    history: list | None = None,
    tools: list[Tool] | None = None,
) -> tuple[list[object], AssistantMessage]:
    stream_fn = create_anthropic_messages_stream(api_key=_api_key())
    stream = stream_fn(
        _model(),
        Context(
            system_prompt="You are a concise test assistant.",
            messages=history
            or [UserMessage(content=prompt, timestamp=0)],
            tools=tools or [],
        ),
        StreamOptions(max_tokens=2048),
    )
    events = [event async for event in stream]
    message = await stream.result()
    if message.stop_reason == "error":
        raise AssertionError(message.error_message or "live request failed")
    return events, message


def test_live_text_stream() -> None:
    if not _enabled():
        return
    events, message = asyncio.run(
        _run("Reply with exactly OK and nothing else.")
    )

    assert events
    assert events[0].type == "start"
    assert events[-1].type == "done"
    assert message.stop_reason == "stop"
    assert any(
        isinstance(block, TextContent) and "OK" in block.text
        for block in message.content
    )
    assert message.usage.output_tokens > 0
    assert message.usage.total_tokens == (
        message.usage.input_tokens
        + message.usage.cache_write_tokens
        + message.usage.cache_read_tokens
        + message.usage.output_tokens
    )
    for block in message.content:
        if isinstance(block, ThinkingContent) and not block.redacted:
            assert block.signature


def test_live_tool_round_trip() -> None:
    if not _enabled():
        return

    tool = Tool(
        name="echo_value",
        description="Echo a value back to the caller.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )
    prompt = (
        "Call the echo_value tool exactly once with value 'ping'. "
        "Do not answer directly."
    )
    _, first = asyncio.run(_run(prompt, tools=[tool]))

    calls = [block for block in first.content if isinstance(block, ToolCall)]
    assert calls, "model did not call the tool"
    results = [
        ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=[TextContent("pong")],
            is_error=False,
            timestamp=1,
        )
        for call in calls
    ]
    _, second = asyncio.run(
        _run(
            prompt,
            history=[
                UserMessage(content=prompt, timestamp=0),
                first,
                *results,
            ],
            tools=[tool],
        )
    )

    assert second.stop_reason in ("stop", "tool_use")
    assert any(
        isinstance(block, TextContent) and block.text.strip()
        for block in second.content
    )


if __name__ == "__main__":
    if not _enabled():
        print(
            "set LING_LIVE_ANTHROPIC=1 and LING_API_KEY/"
            "DEEPSEEK_API_KEY to run live tests"
        )
        raise SystemExit(0)
    test_live_text_stream()
    test_live_tool_round_trip()
    print("all live Anthropic tests passed")

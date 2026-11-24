"""Tests for the OpenAI Responses adapter."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ling_ai.adapters._http import _AttemptState
from ling_ai.adapters.openai_responses import _build_payload, _consume_sse
from ling_ai.stream import AssistantMessageEventStream, StreamOptions
from ling_ai.types import Context, Model, TextContent, ThinkingContent, Tool, UserMessage


MODEL = Model(
    id="gpt-6.1-sol",
    provider="openai",
    api="openai-responses",
    base_url="http://example.test/v1",
)


async def _lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_build_payload() -> None:
    payload = _build_payload(
        MODEL,
        Context(
            system_prompt="You are a coding agent.",
            messages=[UserMessage(content="read a.py", timestamp=0)],
            tools=[Tool(name="read_file", description="Read a file", parameters={"type": "object"})],
        ),
        StreamOptions(reasoning="high", max_tokens=100),
    )

    assert payload["model"] == "gpt-6.1-sol"
    assert payload["instructions"] == "You are a coding agent."
    assert payload["input"] == [{"role": "user", "content": "read a.py"}]
    assert payload["tools"][0]["type"] == "function"
    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
    assert payload["max_output_tokens"] == 100


def test_summary_requested_with_default_effort() -> None:
    payload = _build_payload(MODEL, Context(), StreamOptions())
    assert payload["reasoning"] == {"summary": "auto"}


async def _consume_summary() -> tuple[list[object], object]:
    stream = AssistantMessageEventStream()
    await _consume_sse(
        _lines(
            'data: {"type":"response.reasoning_summary_text.delta","delta":"Check "}',
            'data: {"type":"response.reasoning_summary_text.delta","delta":"the inputs."}',
            'data: {"type":"response.reasoning_summary_text.done","text":"Check the inputs."}',
            'data: {"type":"response.output_text.delta","delta":"Answer"}',
            'data: {"type":"response.completed","response":{"status":"completed"}}',
        ),
        stream,
        model=MODEL,
        options=StreamOptions(),
        attempt_state=_AttemptState(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_reasoning_summary_stream() -> None:
    events, message = asyncio.run(_consume_summary())
    assert [event.type for event in events] == [
        "start", "thinking_start", "thinking_delta", "thinking_delta",
        "thinking_end", "text_start", "text_delta", "text_end", "done",
    ]
    assert isinstance(message.content[0], ThinkingContent)
    assert message.content[0].thinking == "Check the inputs."
    assert events[4].content == "Check the inputs."
    assert message.content[1].text == "Answer"


async def _consume() -> tuple[list[object], object]:
    stream = AssistantMessageEventStream()
    await _consume_sse(
        _lines(
            'data: {"type":"response.created","response":{"id":"resp_1"}}',
            'data: {"type":"response.output_text.delta","delta":"hello"}',
            'data: {"type":"response.output_item.added","item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"read_file"}}',
            'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"{\\"path\\":\\"a.py\\"}"}',
            'data: {"type":"response.output_item.done","item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"read_file","arguments":"{\\"path\\":\\"a.py\\"}"}}',
            'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":3,"output_tokens":4,"total_tokens":7}}}',
        ),
        stream,
        model=MODEL,
        options=StreamOptions(),
        attempt_state=_AttemptState(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_stream_text_and_tool_call() -> None:
    events, message = asyncio.run(_consume())
    assert [event.type for event in events] == [
        "start", "text_start", "text_delta", "toolcall_start",
        "toolcall_delta", "toolcall_end", "text_end", "done",
    ]
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "hello"
    assert message.usage.total_tokens == 7
    assert message.stop_reason == "tool_use"
    assert message.content[1].id == "call_1"
    assert message.content[1].arguments == {"path": "a.py"}


if __name__ == "__main__":
    test_build_payload()
    test_summary_requested_with_default_effort()
    test_reasoning_summary_stream()
    test_stream_text_and_tool_call()
    print("all tests passed")

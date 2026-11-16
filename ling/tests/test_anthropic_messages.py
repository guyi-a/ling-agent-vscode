"""Tests for the Anthropic Messages adapter."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from ling_ai.adapters._http import _Aborted
from ling_ai.adapters.anthropic_messages import (
    _build_payload,
    _consume_sse,
    _messages_url,
    create_anthropic_messages_stream,
)
from ling_ai.stream import AssistantMessageEventStream, StreamOptions
from ling_ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

MODEL = Model(
    id="deepseek-flash",
    provider="deepseek",
    api="anthropic-messages",
    base_url="https://api.deepseek.com/anthropic",
)


async def _lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


def _data(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def _message_start(
    *,
    input_tokens: int = 10,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> str:
    return _data(
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "deepseek-flash",
                "content": [],
                "stop_reason": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_creation_input_tokens": cache_write_tokens,
                    "cache_read_input_tokens": cache_read_tokens,
                    "output_tokens": 0,
                },
            },
        }
    )


def _message_delta(
    *,
    stop_reason: str | None = "end_turn",
    output_tokens: int = 2,
    input_tokens: int | None = None,
) -> str:
    payload: dict = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason},
        "usage": {"output_tokens": output_tokens},
    }
    if input_tokens is not None:
        payload["usage"]["input_tokens"] = input_tokens
    return _data(payload)


async def _collect(
    sse: list[str],
    *,
    options: StreamOptions | None = None,
) -> tuple[list[object], AssistantMessage]:
    stream = AssistantMessageEventStream()
    await _consume_sse(
        _lines(*sse),
        stream,
        model=MODEL,
        options=options or StreamOptions(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_build_payload_basic() -> None:
    context = Context(
        system_prompt="You are a coding agent.",
        messages=[
            UserMessage(content="read a.py", timestamp=0),
            AssistantMessage(
                content=[TextContent("ok")],
                provider="deepseek",
                model="deepseek-flash",
                usage=Usage(),
                stop_reason="stop",
                timestamp=1,
            ),
        ],
    )

    payload = _build_payload(
        MODEL,
        context,
        StreamOptions(temperature=0.2, max_tokens=100),
    )

    assert payload["model"] == "deepseek-flash"
    assert payload["max_tokens"] == 100
    assert payload["stream"] is True
    assert payload["system"] == "You are a coding agent."
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "read a.py"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
        },
    ]
    assert "temperature" not in payload
    assert "thinking" not in payload


def test_build_payload_tools_and_reasoning() -> None:
    context = Context(
        messages=[UserMessage(content="hi", timestamp=0)],
        tools=[
            Tool(
                name="read_file",
                description="Read a file",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
            Tool(name="empty", description="No parameters"),
        ],
    )

    payload = _build_payload(
        MODEL,
        context,
        StreamOptions(reasoning="high"),
    )

    assert payload["max_tokens"] == 8192
    assert payload["tool_choice"] == {"type": "auto"}
    assert payload["tools"][0] == {
        "name": "read_file",
        "description": "Read a file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    }
    assert payload["tools"][1]["input_schema"] == {
        "type": "object",
        "properties": {},
    }
    assert "type" not in payload["tools"][0]
    assert payload["output_config"] == {"effort": "high"}


def test_max_tokens_precedence() -> None:
    context = Context(messages=[UserMessage(content="hi", timestamp=0)])
    assert _build_payload(
        MODEL,
        context,
        StreamOptions(max_tokens=123),
    )["max_tokens"] == 123

    model = Model(
        id="deepseek-flash",
        provider="deepseek",
        api="anthropic-messages",
        max_output_tokens=456,
    )
    assert _build_payload(
        model,
        context,
        StreamOptions(),
    )["max_tokens"] == 456


def test_tool_result_grouping() -> None:
    context = Context(
        messages=[
            UserMessage(content="read both", timestamp=0),
            AssistantMessage(
                content=[
                    TextContent("calling"),
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "a.py"},
                    ),
                    ToolCall(
                        id="call_2",
                        name="read_file",
                        arguments={"path": "b.py"},
                    ),
                ],
                provider="deepseek",
                model="deepseek-flash",
                usage=Usage(),
                stop_reason="tool_use",
                timestamp=1,
            ),
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="read_file",
                content=[TextContent("print(1)")],
                is_error=False,
                timestamp=2,
            ),
            ToolResultMessage(
                tool_call_id="call_2",
                tool_name="read_file",
                content=[TextContent("print(2)")],
                is_error=True,
                timestamp=3,
            ),
            UserMessage(content="continue", timestamp=4),
        ]
    )

    messages = _build_payload(
        MODEL,
        context,
        StreamOptions(),
    )["messages"]

    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert [block["type"] for block in messages[1]["content"]] == [
        "text",
        "tool_use",
        "tool_use",
    ]
    assert messages[2]["role"] == "user"
    assert [block["type"] for block in messages[2]["content"]] == [
        "tool_result",
        "tool_result",
        "text",
    ]
    assert messages[2]["content"][0]["tool_use_id"] == "call_1"
    assert messages[2]["content"][1]["is_error"] is True
    assert messages[2]["content"][2]["text"] == "continue"


def test_tool_result_images() -> None:
    context = Context(
        messages=[
            UserMessage(content="read", timestamp=0),
            AssistantMessage(
                content=[
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "a.png"},
                    )
                ],
                provider="deepseek",
                model="deepseek-flash",
                usage=Usage(),
                stop_reason="tool_use",
                timestamp=1,
            ),
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="read_file",
                content=[
                    TextContent("image:"),
                    ImageContent(data="abc", mime_type="image/png"),
                ],
                is_error=False,
                timestamp=2,
            ),
        ]
    )

    messages = _build_payload(
        MODEL,
        context,
        StreamOptions(),
    )["messages"]

    content = messages[-1]["content"][0]["content"]
    assert content[0] == {"type": "text", "text": "image:"}
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "abc",
        },
    }


def test_drop_unpaired_and_errored_tool_calls() -> None:
    context = Context(
        messages=[
            UserMessage(content="start", timestamp=0),
            AssistantMessage(
                content=[
                    TextContent("partial"),
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "a.py"},
                    ),
                ],
                provider="deepseek",
                model="deepseek-flash",
                usage=Usage(),
                stop_reason="error",
                timestamp=1,
            ),
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="read_file",
                content=[TextContent("too late")],
                is_error=False,
                timestamp=2,
            ),
        ]
    )

    messages = _build_payload(
        MODEL,
        context,
        StreamOptions(),
    )["messages"]

    assert len(messages) == 2
    assert messages[1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "partial"}],
    }
    assert all(
        block.get("type") != "tool_result"
        for message in messages
        for block in message["content"]
    )


def test_orphan_tool_result_is_dropped() -> None:
    context = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="read_file",
                content=[TextContent("orphan")],
                is_error=False,
                timestamp=0,
            ),
            UserMessage(content="hello", timestamp=1),
        ]
    )

    messages = _build_payload(
        MODEL,
        context,
        StreamOptions(),
    )["messages"]

    assert messages == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        }
    ]


def test_thinking_replay() -> None:
    context = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(
                        thinking="plan",
                        signature="anthropic-signature",
                    ),
                    ThinkingContent(
                        thinking="",
                        redacted=True,
                        data="opaque",
                    ),
                    TextContent("answer"),
                ],
                provider="deepseek",
                model="deepseek-flash",
                usage=Usage(),
                stop_reason="stop",
                timestamp=0,
                api="anthropic-messages",
            )
        ]
    )

    messages = _build_payload(
        MODEL,
        context,
        StreamOptions(),
    )["messages"]

    assert messages[0]["content"] == [
        {
            "type": "thinking",
            "thinking": "plan",
            "signature": "anthropic-signature",
        },
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "answer"},
    ]

    openai = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(
                        thinking="openai reasoning",
                        signature="reasoning_content",
                    ),
                    TextContent("answer"),
                ],
                provider="deepseek",
                model="deepseek-chat",
                usage=Usage(),
                stop_reason="stop",
                timestamp=0,
                api="openai-completions",
            )
        ]
    )
    openai_messages = _build_payload(
        MODEL,
        openai,
        StreamOptions(),
    )["messages"]
    assert openai_messages[0]["content"] == [
        {"type": "text", "text": "answer"}
    ]


def test_endpoint_resolution() -> None:
    assert (
        _messages_url("https://api.deepseek.com/anthropic")
        == "https://api.deepseek.com/anthropic/v1/messages"
    )
    assert (
        _messages_url("https://api.deepseek.com/anthropic/v1")
        == "https://api.deepseek.com/anthropic/v1/messages"
    )
    assert (
        _messages_url("https://api.anthropic.com/v1/messages")
        == "https://api.anthropic.com/v1/messages"
    )


def test_text_stream() -> None:
    async def run() -> tuple[list[object], AssistantMessage]:
        return await _collect(
            [
                _message_start(input_tokens=10),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hello"},
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": " world"},
                    }
                ),
                _data({"type": "content_block_stop", "index": 0}),
                _message_delta(output_tokens=2),
                _data({"type": "message_stop"}),
            ]
        )

    events, message = asyncio.run(run())

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    assert message.content[0].text == "Hello world"
    assert message.stop_reason == "stop"
    assert message.usage.input_tokens == 10
    assert message.usage.output_tokens == 2
    assert message.usage.total_tokens == 12


def test_thinking_stream() -> None:
    async def run() -> tuple[list[object], AssistantMessage]:
        return await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "thinking", "thinking": ""},
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": "weigh ",
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": "options",
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "signature_delta",
                            "signature": "sig-",
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "signature_delta",
                            "signature": "part",
                        },
                    }
                ),
                _data({"type": "content_block_stop", "index": 0}),
                _message_delta(),
                _data({"type": "message_stop"}),
            ]
        )

    events, message = asyncio.run(run())

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "done",
    ]
    assert message.content[0].thinking == "weigh options"
    assert message.content[0].signature == "sig-part"


def test_redacted_thinking_stream() -> None:
    async def run() -> tuple[list[object], AssistantMessage]:
        return await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "redacted_thinking",
                            "data": "opaque",
                        },
                    }
                ),
                _data({"type": "content_block_stop", "index": 0}),
                _message_delta(),
                _data({"type": "message_stop"}),
            ]
        )

    events, message = asyncio.run(run())

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_end",
        "done",
    ]
    assert message.content[0].redacted is True
    assert message.content[0].data == "opaque"


def test_tool_call_stream() -> None:
    async def run() -> tuple[list[object], AssistantMessage]:
        return await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "read_file",
                            "input": {},
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"path":',
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '"a.py"}',
                        },
                    }
                ),
                _data({"type": "content_block_stop", "index": 0}),
                _message_delta(stop_reason="tool_use", output_tokens=5),
                _data({"type": "message_stop"}),
            ]
        )

    events, message = asyncio.run(run())

    assert [event.type for event in events] == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]
    assert message.stop_reason == "tool_use"
    assert len(message.content) == 1
    call = message.content[0]
    assert call.id == "call_1"
    assert call.name == "read_file"
    assert call.arguments == {"path": "a.py"}


def test_parallel_tool_calls_stream() -> None:
    async def run() -> AssistantMessage:
        _, message = await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "read_file",
                            "input": {},
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {
                            "type": "tool_use",
                            "id": "call_2",
                            "name": "read_file",
                            "input": {},
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"path":"b.py"}',
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"path":"a.py"}',
                        },
                    }
                ),
                _data({"type": "content_block_stop", "index": 0}),
                _data({"type": "content_block_stop", "index": 1}),
                _message_delta(stop_reason="tool_use"),
                _data({"type": "message_stop"}),
            ]
        )
        return message

    message = asyncio.run(run())

    assert [call.id for call in message.content] == ["call_1", "call_2"]
    assert message.content[0].arguments == {"path": "a.py"}
    assert message.content[1].arguments == {"path": "b.py"}


def test_stop_reason_mapping() -> None:
    async def reason(value: str) -> AssistantMessage:
        _, message = await _collect(
            [
                _message_start(),
                _message_delta(stop_reason=value),
                _data({"type": "message_stop"}),
            ]
        )
        return message

    assert asyncio.run(reason("end_turn")).stop_reason == "stop"
    assert asyncio.run(reason("stop_sequence")).stop_reason == "stop"
    assert asyncio.run(reason("max_tokens")).stop_reason == "length"
    assert asyncio.run(reason("tool_use")).stop_reason == "tool_use"
    assert asyncio.run(reason("pause_turn")).stop_reason == "stop"

    async def unknown() -> None:
        await _collect(
            [
                _message_start(),
                _message_delta(stop_reason="mystery"),
                _data({"type": "message_stop"}),
            ]
        )

    try:
        asyncio.run(unknown())
    except ValueError as exc:
        assert "mystery" in str(exc)
    else:
        raise AssertionError("unknown stop reason should raise")

    async def refusal() -> None:
        await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": "refusal",
                            "stop_details": {"explanation": "blocked"},
                        },
                        "usage": {"output_tokens": 0},
                    }
                ),
                _data({"type": "message_stop"}),
            ]
        )

    try:
        asyncio.run(refusal())
    except RuntimeError as exc:
        assert "refusal" in str(exc)
    else:
        raise AssertionError("refusal should raise")


def test_usage_uses_final_message_delta() -> None:
    async def run() -> AssistantMessage:
        _, message = await _collect(
            [
                _message_start(
                    input_tokens=10,
                    cache_write_tokens=2,
                    cache_read_tokens=3,
                ),
                _message_delta(
                    output_tokens=4,
                    input_tokens=12,
                ),
                _data({"type": "message_stop"}),
            ]
        )
        return message

    message = asyncio.run(run())

    assert message.usage.input_tokens == 12
    assert message.usage.output_tokens == 4
    assert message.usage.cache_write_tokens == 2
    assert message.usage.cache_read_tokens == 3
    assert message.usage.total_tokens == 21


def test_missing_message_stop() -> None:
    async def run() -> None:
        await _collect(
            [
                _message_start(),
                _message_delta(),
            ]
        )

    try:
        asyncio.run(run())
    except RuntimeError as exc:
        assert "message_stop" in str(exc)
    else:
        raise AssertionError("missing message_stop should raise")


def test_stream_error_frame() -> None:
    async def run() -> None:
        await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "error",
                        "error": {
                            "type": "overloaded_error",
                            "message": "busy",
                        },
                    }
                ),
            ]
        )

    try:
        asyncio.run(run())
    except RuntimeError as exc:
        assert "busy" in str(exc)
    else:
        raise AssertionError("error frame should raise")


def test_invalid_tool_json() -> None:
    async def run() -> None:
        await _collect(
            [
                _message_start(),
                _data(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "read_file",
                            "input": {},
                        },
                    }
                ),
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": "{bad",
                        },
                    }
                ),
                _data({"type": "content_block_stop", "index": 0}),
                _message_delta(stop_reason="tool_use"),
                _data({"type": "message_stop"}),
            ]
        )

    try:
        asyncio.run(run())
    except ValueError as exc:
        assert "Invalid JSON" in str(exc)
    else:
        raise AssertionError("invalid tool JSON should raise")


def test_ping_and_comments_are_ignored() -> None:
    async def run() -> AssistantMessage:
        _, message = await _collect(
            [
                ": keep-alive",
                _message_start(),
                'event: ping\ndata: {"type":"ping"}',
                _data({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                _data({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
                _data({"type": "content_block_stop", "index": 0}),
                _message_delta(),
                _data({"type": "message_stop"}),
            ]
        )
        return message

    assert asyncio.run(run()).content[0].text == "ok"


async def _http_success() ->tuple[list[object], AssistantMessage]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="\n".join(
                [
                    _message_start(),
                    _data({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                    _data({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
                    _data({"type": "content_block_stop", "index": 0}),
                    _message_delta(),
                    _data({"type": "message_stop"}),
                ]
            ),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_anthropic_messages_stream(
            api_key="test-key",
            client=client,
        )
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(),
        )
        events = [event async for event in stream]
        message = await stream.result()
        assert len(requests) == 1
        assert requests[0].url.path == "/anthropic/v1/messages"
        assert requests[0].headers["x-api-key"] == "test-key"
        assert requests[0].headers["anthropic-version"] == "2023-06-01"
        return events, message


def test_http_success() -> None:
    events, message = asyncio.run(_http_success())
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    assert message.content[0].text == "ok"


def test_http_error() -> None:
    async def run() -> AssistantMessage:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="bad news")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            stream_fn = create_anthropic_messages_stream(
                api_key="test-key",
                client=client,
            )
            stream = stream_fn(
                MODEL,
                Context(messages=[UserMessage(content="hi", timestamp=0)]),
                StreamOptions(max_retries=0),
            )
            _ = [event async for event in stream]
            return await stream.result()

    message = asyncio.run(run())
    assert message.stop_reason == "error"
    assert "HTTP 500" in (message.error_message or "")


def test_retry_before_output() -> None:
    async def run() -> tuple[int, list[object], AssistantMessage]:
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                return httpx.Response(500, text="temporary")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="\n".join(
                    [
                        _message_start(),
                        _message_delta(),
                        _data({"type": "message_stop"}),
                    ]
                ),
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            stream_fn = create_anthropic_messages_stream(
                api_key="test-key",
                client=client,
            )
            stream = stream_fn(
                MODEL,
                Context(messages=[UserMessage(content="hi", timestamp=0)]),
                StreamOptions(max_retries=1, retry_base_delay=0),
            )
            events = [event async for event in stream]
            return requests, events, await stream.result()

    requests, events, message = asyncio.run(run())
    assert requests == 2
    assert [event.type for event in events].count("start") == 1
    assert message.stop_reason == "stop"


def test_cancel_before_request() -> None:
    async def run() -> tuple[int, AssistantMessage]:
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, text="")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            cancel = asyncio.Event()
            cancel.set()
            stream_fn = create_anthropic_messages_stream(
                api_key="test-key",
                client=client,
            )
            stream = stream_fn(
                MODEL,
                Context(messages=[UserMessage(content="hi", timestamp=0)]),
                StreamOptions(cancel_event=cancel),
            )
            events = [event async for event in stream]
            return requests, await stream.result()

    requests, message = asyncio.run(run())
    assert requests == 0
    assert message.stop_reason == "aborted"


async def _abort_stream() -> type[BaseException] | None:
    stream = AssistantMessageEventStream()
    cancel = asyncio.Event()
    cancel.set()
    try:
        await _consume_sse(
            _lines(
                _data(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": "x",
                        },
                    }
                )
            ),
            stream,
            model=MODEL,
            options=StreamOptions(cancel_event=cancel),
        )
    except BaseException as exc:
        return type(exc)
    return None


def test_abort() -> None:
    assert asyncio.run(_abort_stream()) is _Aborted


async def _cancel_while_waiting_for_headers() -> AssistantMessage:
    request_started = asyncio.Event()

    class StallingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(
            self,
            request: httpx.Request,
        ) -> httpx.Response:
            request_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    cancel = asyncio.Event()
    transport = StallingTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_anthropic_messages_stream(
            api_key="test-key",
            client=client,
        )
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(cancel_event=cancel, max_retries=0),
        )
        result_task = asyncio.create_task(stream.result())
        await asyncio.wait_for(request_started.wait(), timeout=1)
        cancel.set()
        return await asyncio.wait_for(result_task, timeout=1)


def test_cancel_while_waiting_for_headers() -> None:
    message = asyncio.run(_cancel_while_waiting_for_headers())

    assert message.stop_reason == "aborted"
    assert message.error_message == "Request aborted"


def test_non_retryable_http_error() -> None:
    async def run() -> tuple[int, AssistantMessage]:
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(401, text="unauthorized")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            stream_fn = create_anthropic_messages_stream(
                api_key="test-key",
                client=client,
            )
            stream = stream_fn(
                MODEL,
                Context(messages=[UserMessage(content="hi", timestamp=0)]),
                StreamOptions(max_retries=2, retry_base_delay=0),
            )
            _ = [event async for event in stream]
            return requests, await stream.result()

    requests, message = asyncio.run(run())
    assert requests == 1
    assert message.stop_reason == "error"
    assert "HTTP 401" in (message.error_message or "")


def test_no_retry_after_output() -> None:
    async def run() -> tuple[int, AssistantMessage]:
        requests = 0

        class BrokenSSEStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"type":"message_start","message":'
                    b'{"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
                )
                yield (
                    b'data: {"type":"content_block_start","index":0,'
                    b'"content_block":{"type":"text","text":""}}\n\n'
                )
                yield (
                    b'data: {"type":"content_block_delta","index":0,'
                    b'"delta":{"type":"text_delta","text":"partial"}}\n\n'
                )
                raise httpx.ReadError("connection lost")

            async def aclose(self) -> None:
                pass

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=BrokenSSEStream(),
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            stream_fn = create_anthropic_messages_stream(
                api_key="test-key",
                client=client,
            )
            stream = stream_fn(
                MODEL,
                Context(messages=[UserMessage(content="hi", timestamp=0)]),
                StreamOptions(max_retries=2, retry_base_delay=0),
            )
            events = [event async for event in stream]
            return requests, await stream.result()

    requests, message = asyncio.run(run())
    assert requests == 1
    assert message.stop_reason == "error"
    assert "connection lost" in (message.error_message or "")
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "partial"


def test_malformed_sse() -> None:
    async def run() -> None:
        await _collect(
            [
                _message_start(),
                "data: {not json",
            ]
        )

    try:
        asyncio.run(run())
    except ValueError as exc:
        assert "Invalid SSE data" in str(exc)
    else:
        raise AssertionError("malformed SSE should raise")


if __name__ == "__main__":
    test_build_payload_basic()
    test_build_payload_tools_and_reasoning()
    test_max_tokens_precedence()
    test_tool_result_grouping()
    test_tool_result_images()
    test_drop_unpaired_and_errored_tool_calls()
    test_orphan_tool_result_is_dropped()
    test_thinking_replay()
    test_endpoint_resolution()
    test_text_stream()
    test_thinking_stream()
    test_redacted_thinking_stream()
    test_tool_call_stream()
    test_parallel_tool_calls_stream()
    test_stop_reason_mapping()
    test_usage_uses_final_message_delta()
    test_missing_message_stop()
    test_stream_error_frame()
    test_invalid_tool_json()
    test_ping_and_comments_are_ignored()
    test_http_success()
    test_http_error()
    test_retry_before_output()
    test_cancel_before_request()
    test_abort()
    test_cancel_while_waiting_for_headers()
    test_non_retryable_http_error()
    test_no_retry_after_output()
    test_malformed_sse()
    print("all tests passed")

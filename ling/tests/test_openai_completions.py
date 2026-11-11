"""Tests for the OpenAI-compatible adapter."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import AsyncIterator

import httpx

from ling_ai.adapters.openai_completions import (
    _Aborted,
    _build_payload,
    _consume_sse,
    create_openai_completions_stream,
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
    id="deepseek-chat",
    provider="deepseek",
    api="openai-completions",
    base_url="https://api.deepseek.com/v1",
)


async def _lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_build_payload() -> None:
    context = Context(
        system_prompt="You are a coding agent.",
        messages=[
            UserMessage(content="read a.py", timestamp=0),
            AssistantMessage(
                content=[
                    TextContent("ok"),
                    ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"}),
                ],
                provider="deepseek",
                model="deepseek-chat",
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
        ],
        tools=[
            Tool(
                name="read_file",
                description="Read a file",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            )
        ],
    )

    payload = _build_payload(
        MODEL,
        context,
        StreamOptions(temperature=0.2, max_tokens=100),
    )

    assert payload["model"] == "deepseek-chat"
    assert payload["messages"][0] == {
        "role": "system",
        "content": "You are a coding agent.",
    }
    assert payload["messages"][1] == {"role": "user", "content": "read a.py"}

    assistant = payload["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "ok"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "path": "a.py"
    }

    assert payload["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "print(1)",
    }
    assert payload["tools"][0]["function"]["name"] == "read_file"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 100


def test_build_payload_skips_empty_assistant_messages() -> None:
    context = Context(
        messages=[
            UserMessage(content="first", timestamp=0),
            AssistantMessage(
                content=[],
                provider="deepseek",
                model="deepseek-chat",
                usage=Usage(),
                stop_reason="aborted",
                timestamp=1,
            ),
            UserMessage(content="second", timestamp=2),
        ]
    )

    payload = _build_payload(MODEL, context, StreamOptions())

    assert [message["role"] for message in payload["messages"]] == [
        "user",
        "user",
    ]


def test_reasoning_payload() -> None:
    context = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent("internal reasoning", signature="reasoning_content"),
                    TextContent("visible answer"),
                ],
                provider="deepseek",
                model="deepseek-chat",
                usage=Usage(),
                stop_reason="stop",
                timestamp=1,
            )
        ]
    )

    payload = _build_payload(
        MODEL,
        context,
        StreamOptions(reasoning="high"),
    )

    assistant = payload["messages"][0]
    assert assistant["content"] == "visible answer"
    assert assistant["reasoning_content"] == "internal reasoning"
    assert payload["reasoning_effort"] == "high"


def test_compat_payload() -> None:
    compat_model = replace(
        MODEL,
        compat={
            "supports_developer_role": True,
            "supports_reasoning_effort": False,
            "supports_usage_in_streaming": False,
            "supports_tool_choice": False,
            "max_tokens_field": "max_completion_tokens",
        },
    )
    context = Context(
        system_prompt="You are a coding agent.",
        messages=[UserMessage(content="hi", timestamp=0)],
        tools=[Tool(name="read_file", description="Read a file")],
    )

    payload = _build_payload(
        compat_model,
        context,
        StreamOptions(max_tokens=128, reasoning="high"),
    )

    assert payload["messages"][0]["role"] == "developer"
    assert "stream_options" not in payload
    assert "tool_choice" not in payload
    assert payload["max_completion_tokens"] == 128
    assert "max_tokens" not in payload
    assert "reasoning_effort" not in payload


def test_compat_tool_result_messages() -> None:
    compat_model = replace(
        MODEL,
        compat={
            "requires_tool_result_name": True,
            "requires_assistant_after_tool_result": True,
        },
    )
    context = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="read_file",
                content=[TextContent("contents")],
                is_error=False,
                timestamp=1,
            ),
            UserMessage(content="continue", timestamp=2),
        ]
    )

    payload = _build_payload(compat_model, context, StreamOptions())

    assert [message["role"] for message in payload["messages"]] == [
        "tool",
        "assistant",
        "user",
    ]
    assert payload["messages"][0]["name"] == "read_file"


def test_tool_result_images_payload() -> None:
    context = Context(
        messages=[
            AssistantMessage(
                content=[
                    ToolCall(id="call_1", name="take_screenshot", arguments={}),
                ],
                provider="deepseek",
                model="deepseek-chat",
                usage=Usage(),
                stop_reason="tool_use",
                timestamp=1,
            ),
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="take_screenshot",
                content=[
                    TextContent("screenshot ready"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                ],
                is_error=False,
                timestamp=2,
            ),
        ]
    )

    payload = _build_payload(MODEL, context, StreamOptions())

    assert payload["messages"][1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "screenshot ready",
    }
    image_message = payload["messages"][2]
    assert image_message["role"] == "user"
    assert image_message["content"][0]["type"] == "text"
    assert image_message["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aW1n"},
    }

    image_only = _build_payload(
        MODEL,
        Context(
            messages=[
                ToolResultMessage(
                    tool_call_id="call_2",
                    tool_name="take_screenshot",
                    content=[ImageContent(data="aW1n", mime_type="image/png")],
                    is_error=False,
                    timestamp=3,
                )
            ]
        ),
        StreamOptions(),
    )
    assert image_only["messages"][0]["content"] == "(see attached image)"
    assert image_only["messages"][1]["content"][1]["type"] == "image_url"


async def _collect_text_stream() -> tuple[list[object], AssistantMessage]:
    stream = AssistantMessageEventStream()
    sse = [
        'data: {"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":" world"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}',
        "data: [DONE]",
    ]
    await _consume_sse(
        _lines(*sse),
        stream,
        model=MODEL,
        options=StreamOptions(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_text_stream() -> None:
    events, message = asyncio.run(_collect_text_stream())

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


async def _collect_reasoning_stream() -> tuple[list[object], AssistantMessage]:
    stream = AssistantMessageEventStream()
    sse = [
        (
            'data: {"choices":[{"delta":'
            '{"reasoning_content":"think "},"finish_reason":null}]}'
        ),
        (
            'data: {"choices":[{"delta":'
            '{"reasoning_content":"again"},"finish_reason":null}]}'
        ),
        (
            'data: {"choices":[{"delta":'
            '{"content":"answer"},"finish_reason":null}]}'
        ),
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    await _consume_sse(
        _lines(*sse),
        stream,
        model=MODEL,
        options=StreamOptions(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_reasoning_stream() -> None:
    events, message = asyncio.run(_collect_reasoning_stream())

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    thinking = message.content[0]
    assert isinstance(thinking, ThinkingContent)
    assert thinking.thinking == "think again"
    assert thinking.signature == "reasoning_content"
    assert isinstance(message.content[1], TextContent)
    assert message.content[1].text == "answer"


async def _collect_finish_reason_error(
    finish_reason: str | None,
) -> tuple[list[object], AssistantMessage]:
    stream = AssistantMessageEventStream()
    finish = (
        f'"finish_reason":"{finish_reason}"'
        if finish_reason is not None
        else '"finish_reason":null'
    )
    sse = [
        'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}',
        f'data: {{"choices":[{{"delta":{{}},{finish}}}]}}',
        "data: [DONE]",
    ]
    await _consume_sse(
        _lines(*sse),
        stream,
        model=MODEL,
        options=StreamOptions(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_finish_reason_errors() -> None:
    for reason, expected in (
        ("content_filter", "Provider finish_reason: content_filter"),
        ("unknown_reason", "Provider finish_reason: unknown_reason"),
        (None, "Stream ended without finish_reason"),
    ):
        events, message = asyncio.run(_collect_finish_reason_error(reason))
        assert events[-1].type == "error"
        assert message.stop_reason == "error"
        assert message.error_message == expected
        assert isinstance(message.content[0], TextContent)
        assert message.content[0].text == "partial"


async def _infer_finish_reason(include_tool: bool) -> AssistantMessage:
    stream = AssistantMessageEventStream()
    if include_tool:
        delta = (
            '{"tool_calls":[{"index":0,"id":"call_1","function":'
            '{"name":"read_file","arguments":"{\\"path\\":\\"a.py\\"}"}}]}'
        )
    else:
        delta = '{"content":"done"}'
    sse = [
        f'data: {{"choices":[{{"delta":{delta},"finish_reason":null}}]}}',
        "data: [DONE]",
    ]
    await _consume_sse(
        _lines(*sse),
        stream,
        model=replace(
            MODEL,
            compat={"supports_finish_reason": False},
        ),
        options=StreamOptions(),
    )
    return await stream.result()


def test_finish_reason_inference_compat() -> None:
    text_message = asyncio.run(_infer_finish_reason(include_tool=False))
    tool_message = asyncio.run(_infer_finish_reason(include_tool=True))

    assert text_message.stop_reason == "stop"
    assert tool_message.stop_reason == "tool_use"
    assert isinstance(tool_message.content[0], ToolCall)


async def _collect_tool_call_stream() -> tuple[list[object], AssistantMessage]:
    stream = AssistantMessageEventStream()
    sse = [
        'data: {"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
        (
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":0,"id":"call_1","type":"function",'
            '"function":{"name":"read_file","arguments":""}}]},'
            '"finish_reason":null}]}'
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":0,"function":{"arguments":"{\\"path\\":"}}]},'
            '"finish_reason":null}]}'
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":0,"function":{"arguments":"\\"a.py\\"}"}}]},'
            '"finish_reason":null}]}'
        ),
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    await _consume_sse(
        _lines(*sse),
        stream,
        model=MODEL,
        options=StreamOptions(),
    )
    events = [event async for event in stream]
    return events, await stream.result()


def test_tool_call_stream() -> None:
    events, message = asyncio.run(_collect_tool_call_stream())

    assert [event.type for event in events] == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]
    deltas = "".join(
        event.delta for event in events if event.type == "toolcall_delta"
    )
    assert deltas == '{"path":"a.py"}'
    assert message.stop_reason == "tool_use"
    assert len(message.content) == 1
    tool_call = message.content[0]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.id == "call_1"
    assert tool_call.name == "read_file"
    assert tool_call.arguments == {"path": "a.py"}


async def _collect_parallel_tool_call_stream() -> AssistantMessage:
    stream = AssistantMessageEventStream()
    sse = [
        (
            'data: {"choices":[{"delta":{"tool_calls":['
            '{"index":0,"id":"call_0","function":{"name":"read_file","arguments":""}},'
            '{"index":1,"id":"call_1","function":{"name":"write_file","arguments":""}}'
            "]},\"finish_reason\":null}]}"
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":1,"function":{"arguments":"{\\"path\\":\\"b.py\\"}"}}]},'
            '"finish_reason":null}]}'
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":0,"function":{"arguments":"{\\"path\\":\\"a.py\\"}"}}]},'
            '"finish_reason":null}]}'
        ),
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        "data: [DONE]",
    ]
    await _consume_sse(
        _lines(*sse),
        stream,
        model=MODEL,
        options=StreamOptions(),
    )
    return await stream.result()


def test_parallel_tool_call_stream() -> None:
    message = asyncio.run(_collect_parallel_tool_call_stream())

    assert message.stop_reason == "tool_use"
    assert [block.id for block in message.content] == ["call_0", "call_1"]
    assert [block.name for block in message.content] == ["read_file", "write_file"]
    assert [block.arguments for block in message.content] == [
        {"path": "a.py"},
        {"path": "b.py"},
    ]


async def _invalid_tool_arguments() -> type[BaseException] | None:
    stream = AssistantMessageEventStream()
    sse = [
        (
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":0,"id":"call_1","function":'
            '{"name":"read_file","arguments":"{bad"}}]},'
            '"finish_reason":"tool_calls"}]}'
        ),
        "data: [DONE]",
    ]
    try:
        await _consume_sse(
            _lines(*sse),
            stream,
            model=MODEL,
            options=StreamOptions(),
        )
    except BaseException as exc:
        return type(exc)
    return None


def test_invalid_tool_arguments() -> None:
    assert asyncio.run(_invalid_tool_arguments()) is ValueError


async def _abort_stream() -> type[BaseException] | None:
    stream = AssistantMessageEventStream()
    cancel = asyncio.Event()
    cancel.set()
    try:
        await _consume_sse(
            _lines('data: {"choices":[{"delta":{"content":"x"}}]}'),
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
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
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


async def _cancel_before_request() -> tuple[int, AssistantMessage]:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200)

    cancel = asyncio.Event()
    cancel.set()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(cancel_event=cancel),
        )
        message = await stream.result()
        return requests, message


def test_cancel_before_request() -> None:
    requests, message = asyncio.run(_cancel_before_request())

    assert requests == 0
    assert message.stop_reason == "aborted"


async def _retry_before_output() -> tuple[int, list[object], AssistantMessage]:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(500, text="temporary failure")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
                "data: [DONE]\n"
            ).encode(),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=1, retry_base_delay=0),
        )
        events = [event async for event in stream]
        return requests, events, await stream.result()


def test_retry_before_output() -> None:
    requests, events, message = asyncio.run(_retry_before_output())

    assert requests == 2
    assert [event.type for event in events].count("start") == 1
    assert message.stop_reason == "stop"
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "ok"


async def _retry_exhausted() -> tuple[int, AssistantMessage]:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500, text="still failing")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=1, retry_base_delay=0),
        )
        message = await stream.result()
        return requests, message


def test_retry_exhausted() -> None:
    requests, message = asyncio.run(_retry_exhausted())

    assert requests == 2
    assert message.stop_reason == "error"
    assert "HTTP 500" in (message.error_message or "")


async def _non_retryable_http_error() -> tuple[int, AssistantMessage]:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=3, retry_base_delay=0),
        )
        message = await stream.result()
        return requests, message


def test_non_retryable_http_error() -> None:
    requests, message = asyncio.run(_non_retryable_http_error())

    assert requests == 1
    assert message.stop_reason == "error"
    assert "HTTP 401" in (message.error_message or "")


async def _no_retry_after_output() -> tuple[int, AssistantMessage]:
    requests = 0

    class BrokenSSEStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                b'data: {"choices":[{"delta":{"content":"partial"},'
                b'"finish_reason":null}]}\n\n'
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
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=2, retry_base_delay=0),
        )
        message = await stream.result()
        return requests, message


def test_no_retry_after_output() -> None:
    requests, message = asyncio.run(_no_retry_after_output())

    assert requests == 1
    assert message.stop_reason == "error"
    assert message.error_message == "connection lost"
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "partial"


async def _malformed_sse() -> tuple[int, AssistantMessage]:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"partial"},'
                '"finish_reason":null}]}\n'
                "data: not-json\n"
            ).encode(),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=2, retry_base_delay=0),
        )
        message = await stream.result()
        return requests, message


def test_malformed_sse() -> None:
    requests, message = asyncio.run(_malformed_sse())

    assert requests == 1
    assert message.stop_reason == "error"
    assert "Invalid SSE data" in (message.error_message or "")
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "partial"


async def _http_success() -> tuple[list[object], AssistantMessage]:
    body = (
        'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
        "data: [DONE]\n"
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=0),
        )
        events = [event async for event in stream]
        message = await stream.result()
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
    assert message.content[0].text == "Hi"


async def _http_error() -> AssistantMessage:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream failed")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream_fn = create_openai_completions_stream(api_key="test-key", client=client)
        stream = stream_fn(
            MODEL,
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            StreamOptions(max_retries=0),
        )
        return await stream.result()


def test_http_error() -> None:
    message = asyncio.run(_http_error())

    assert message.stop_reason == "error"
    assert "HTTP 500" in (message.error_message or "")


if __name__ == "__main__":
    test_build_payload()
    test_reasoning_payload()
    test_compat_payload()
    test_compat_tool_result_messages()
    test_tool_result_images_payload()
    test_text_stream()
    test_reasoning_stream()
    test_finish_reason_errors()
    test_finish_reason_inference_compat()
    test_tool_call_stream()
    test_parallel_tool_call_stream()
    test_invalid_tool_arguments()
    test_abort()
    test_cancel_while_waiting_for_headers()
    test_cancel_before_request()
    test_retry_before_output()
    test_retry_exhausted()
    test_non_retryable_http_error()
    test_no_retry_after_output()
    test_malformed_sse()
    test_http_success()
    test_http_error()
    print("all tests passed")

"""Anthropic Messages API adapter."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

import httpx

from ._http import (
    _Aborted,
    _AttemptState,
    _ProviderHTTPError,
    _await_with_cancel,
    _is_retryable_error,
    _retry_delay,
)
from ..events import (
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ..stream import AssistantMessageEventStream, StreamFunction, StreamOptions
from ..types import (
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

API_NAME = "anthropic-messages"
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MAX_TOKENS = 8192
ANTHROPIC_VERSION = "2023-06-01"

_DONE_REASONS: dict[str, Literal["stop", "length", "tool_use"]] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
    "pause_turn": "stop",
}


@dataclass(slots=True)
class _StreamingToolCall:
    block: ToolCall
    content_index: int
    arguments_json: str = ""


@dataclass(slots=True)
class _BlockState:
    kind: str
    content_index: int
    tool: _StreamingToolCall | None = None
    closed: bool = False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_partial(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        provider=model.provider,
        model=model.id,
        api=API_NAME,
        usage=Usage(),
        stop_reason="pending",
        timestamp=_now_ms(),
    )


def _error_message(
    model: Model,
    message: str,
    *,
    aborted: bool = False,
    partial: AssistantMessage | None = None,
) -> AssistantMessage:
    if partial is not None:
        return AssistantMessage(
            content=partial.content,
            provider=model.provider,
            model=model.id,
            api=API_NAME,
            usage=partial.usage,
            stop_reason="aborted" if aborted else "error",
            error_message=message,
            timestamp=_now_ms(),
        )
    return AssistantMessage(
        content=[],
        provider=model.provider,
        model=model.id,
        api=API_NAME,
        usage=Usage(),
        stop_reason="aborted" if aborted else "error",
        error_message=message,
        timestamp=_now_ms(),
    )


def _messages_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1/messages") or base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _request_headers(
    api_key: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "accept": "text/event-stream",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if extra:
        headers.update(extra)
    return headers


def _max_tokens(model: Model, options: StreamOptions) -> int:
    value = options.max_tokens or model.max_output_tokens or DEFAULT_MAX_TOKENS
    return max(1, int(value))


def _image_block(block: ImageContent) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": block.mime_type,
            "data": block.data,
        },
    }


def _user_content(
    content: str | list[TextContent | ImageContent],
) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []

    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, TextContent):
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            blocks.append(_image_block(block))
    return blocks


def _assistant_content(
    message: AssistantMessage,
    kept_tool_ids: set[str],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextContent):
            if block.text:
                blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent):
            if message.api != API_NAME:
                # OpenAI's signature is a reasoning field name, not an
                # Anthropic signature. Never replay it as one.
                continue
            if block.redacted:
                if block.data:
                    blocks.append(
                        {"type": "redacted_thinking", "data": block.data}
                    )
            elif block.signature:
                blocks.append(
                    {
                        "type": "thinking",
                        "thinking": block.thinking,
                        "signature": block.signature,
                    }
                )
        elif isinstance(block, ToolCall):
            if message.stop_reason in ("error", "aborted"):
                continue
            if block.id and block.name and block.id in kept_tool_ids:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.arguments or {},
                    }
                )
    return blocks


def _tool_result_block(message: ToolResultMessage) -> dict[str, Any]:
    text = "\n".join(
        block.text
        for block in message.content
        if isinstance(block, TextContent)
    )
    images = [
        block
        for block in message.content
        if isinstance(block, ImageContent)
    ]
    content: str | list[dict[str, Any]]
    if images:
        content = [
            {
                "type": "text",
                "text": text or "(see attached image)",
            },
            *(_image_block(image) for image in images),
        ]
    else:
        content = text or "(no tool output)"

    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": content,
    }
    if message.is_error:
        block["is_error"] = True
    return block


def _append_message(
    messages: list[dict[str, Any]],
    role: str,
    blocks: list[dict[str, Any]],
) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": blocks})


def _build_messages(context: Context) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    items = context.messages
    index = 0
    while index < len(items):
        message = items[index]

        if isinstance(message, UserMessage):
            _append_message(messages, "user", _user_content(message.content))
            index += 1
            continue

        if isinstance(message, AssistantMessage):
            result_index = index + 1
            results: list[ToolResultMessage] = []
            while (
                result_index < len(items)
                and isinstance(items[result_index], ToolResultMessage)
            ):
                results.append(items[result_index])
                result_index += 1

            kept_tool_ids = {
                result.tool_call_id
                for result in results
                if result.tool_call_id
            }
            content = _assistant_content(message, kept_tool_ids)
            _append_message(messages, "assistant", content)

            paired = {
                block["id"]
                for block in content
                if block.get("type") == "tool_use"
            }
            result_blocks = [
                _tool_result_block(result)
                for result in results
                if result.tool_call_id in paired
            ]
            _append_message(messages, "user", result_blocks)

            next_index = result_index
            if results and next_index < len(items):
                following = items[next_index]
                if isinstance(following, UserMessage):
                    _append_message(
                        messages,
                        "user",
                        _user_content(following.content),
                    )
                    next_index += 1
            index = next_index
            continue

        if isinstance(message, ToolResultMessage):
            # Orphan result with no immediately preceding assistant turn.
            index += 1
            continue

        raise TypeError(f"Unsupported message type: {type(message)!r}")

    return messages


def _tool_to_wire(tool: Tool) -> dict[str, Any]:
    parameters = tool.parameters or {"type": "object", "properties": {}}
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": parameters,
    }


def _build_payload(
    model: Model,
    context: Context,
    options: StreamOptions,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model.id,
        "max_tokens": _max_tokens(model, options),
        "stream": True,
        "messages": _build_messages(context),
    }
    if context.system_prompt:
        payload["system"] = context.system_prompt
    if context.tools:
        payload["tools"] = [_tool_to_wire(tool) for tool in context.tools]
        payload["tool_choice"] = {"type": "auto"}
    if options.reasoning and options.reasoning != "auto":
        payload["output_config"] = {"effort": options.reasoning}
    return payload


def _apply_usage(usage: Usage, data: dict[str, Any]) -> None:
    fields = (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_creation_input_tokens", "cache_write_tokens"),
        ("cache_read_input_tokens", "cache_read_tokens"),
    )
    for wire_name, attr_name in fields:
        if wire_name in data and data[wire_name] is not None:
            setattr(usage, attr_name, max(0, int(data[wire_name])))
    usage.total_tokens = (
        usage.input_tokens
        + usage.cache_write_tokens
        + usage.cache_read_tokens
        + usage.output_tokens
    )


def _parse_streaming_arguments(arguments_json: str) -> dict[str, Any]:
    if not arguments_json.strip():
        return {}
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _finish_tool_call(tool_call: _StreamingToolCall) -> None:
    if not tool_call.arguments_json.strip():
        if not isinstance(tool_call.block.arguments, dict):
            tool_call.block.arguments = {}
        return
    try:
        arguments = json.loads(tool_call.arguments_json)
    except json.JSONDecodeError as exc:
        name = tool_call.block.name or "<unknown>"
        raise ValueError(
            f"Invalid JSON arguments for tool call {name!r}"
        ) from exc
    if not isinstance(arguments, dict):
        raise ValueError(
            f"Tool call {tool_call.block.name!r} arguments must be a JSON object"
        )
    tool_call.block.arguments = arguments


def _close_block(
    state: _BlockState,
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
) -> None:
    if state.closed:
        return
    state.closed = True

    if state.kind == "text":
        block = partial.content[state.content_index]
        if isinstance(block, TextContent):
            stream.push(
                TextEndEvent(
                    content_index=state.content_index,
                    content=block.text,
                    partial=partial,
                )
            )
        return

    if state.kind == "thinking":
        block = partial.content[state.content_index]
        if isinstance(block, ThinkingContent):
            stream.push(
                ThinkingEndEvent(
                    content_index=state.content_index,
                    content=block.thinking,
                    partial=partial,
                )
            )
        return

    if state.kind == "tool" and state.tool is not None:
        _finish_tool_call(state.tool)
        stream.push(
            ToolCallEndEvent(
                content_index=state.content_index,
                tool_call=state.tool.block,
                partial=partial,
            )
        )


def _start_block(
    wire_index: int,
    block: dict[str, Any],
    blocks: dict[int, _BlockState],
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
) -> None:
    block_type = block.get("type")

    if block_type == "text":
        text = block.get("text")
        value = text if isinstance(text, str) else ""
        partial.content.append(TextContent(value))
        content_index = len(partial.content) - 1
        blocks[wire_index] = _BlockState("text", content_index)
        stream.push(
            TextStartEvent(
                content_index=content_index,
                partial=partial,
            )
        )
        if value:
            stream.push(
                TextDeltaEvent(
                    content_index=content_index,
                    delta=value,
                    partial=partial,
                )
            )
        return

    if block_type == "thinking":
        thinking = block.get("thinking")
        signature = block.get("signature")
        value = thinking if isinstance(thinking, str) else ""
        partial.content.append(
            ThinkingContent(
                thinking=value,
                signature=signature if isinstance(signature, str) else None,
            )
        )
        content_index = len(partial.content) - 1
        blocks[wire_index] = _BlockState("thinking", content_index)
        stream.push(
            ThinkingStartEvent(
                content_index=content_index,
                partial=partial,
            )
        )
        if value:
            stream.push(
                ThinkingDeltaEvent(
                    content_index=content_index,
                    delta=value,
                    partial=partial,
                )
            )
        return

    if block_type == "redacted_thinking":
        data = block.get("data")
        partial.content.append(
            ThinkingContent(
                thinking="",
                redacted=True,
                data=data if isinstance(data, str) else None,
            )
        )
        content_index = len(partial.content) - 1
        blocks[wire_index] = _BlockState("thinking", content_index)
        stream.push(
            ThinkingStartEvent(
                content_index=content_index,
                partial=partial,
            )
        )
        return

    if block_type == "tool_use":
        tool_id = block.get("id")
        name = block.get("name")
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("Anthropic tool_use block is missing id")
        if not isinstance(name, str) or not name:
            raise ValueError("Anthropic tool_use block is missing name")
        input_data = block.get("input")
        arguments = input_data if isinstance(input_data, dict) else {}
        tool_call = ToolCall(id=tool_id, name=name, arguments=arguments)
        partial.content.append(tool_call)
        content_index = len(partial.content) - 1
        streaming = _StreamingToolCall(tool_call, content_index)
        blocks[wire_index] = _BlockState(
            "tool",
            content_index,
            streaming,
        )
        stream.push(
            ToolCallStartEvent(
                content_index=content_index,
                partial=partial,
            )
        )
        return

    blocks[wire_index] = _BlockState("unknown", -1)


def _handle_delta(
    wire_index: int,
    event: dict[str, Any],
    blocks: dict[int, _BlockState],
    stream: AssistantMessageEventStream,
    partial: AssistantMessage,
) -> None:
    state = blocks.get(wire_index)
    if state is None:
        return
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return
    delta_type = delta.get("type")

    if state.kind == "text" and delta_type == "text_delta":
        value = delta.get("text")
        if not isinstance(value, str) or not value:
            return
        block = partial.content[state.content_index]
        if not isinstance(block, TextContent):
            return
        block.text += value
        stream.push(
            TextDeltaEvent(
                content_index=state.content_index,
                delta=value,
                partial=partial,
            )
        )
        return

    if state.kind == "thinking" and delta_type == "thinking_delta":
        value = delta.get("thinking")
        if not isinstance(value, str) or not value:
            return
        block = partial.content[state.content_index]
        if not isinstance(block, ThinkingContent):
            return
        block.thinking += value
        stream.push(
            ThinkingDeltaEvent(
                content_index=state.content_index,
                delta=value,
                partial=partial,
            )
        )
        return

    if state.kind == "thinking" and delta_type == "signature_delta":
        value = delta.get("signature")
        if not isinstance(value, str) or not value:
            return
        block = partial.content[state.content_index]
        if isinstance(block, ThinkingContent):
            block.signature = (block.signature or "") + value
        return

    if state.kind == "tool" and delta_type == "input_json_delta":
        value = delta.get("partial_json")
        if not isinstance(value, str) or not value:
            return
        if state.tool is None:
            return
        state.tool.arguments_json += value
        arguments = _parse_streaming_arguments(state.tool.arguments_json)
        if arguments:
            state.tool.block.arguments = arguments
        stream.push(
            ToolCallDeltaEvent(
                content_index=state.content_index,
                delta=value,
                partial=partial,
            )
        )


async def _consume_sse(
    lines: AsyncIterator[str],
    stream: AssistantMessageEventStream,
    *,
    model: Model,
    options: StreamOptions,
    attempt_state: _AttemptState | None = None,
) -> None:
    if options.cancel_event is not None and options.cancel_event.is_set():
        raise _Aborted

    partial = _new_partial(model)
    if attempt_state is not None:
        attempt_state.started = True
        attempt_state.partial = partial
    stream.push(StartEvent(partial=partial))

    blocks: dict[int, _BlockState] = {}
    stop_reason: str | None = None
    stop_details: Any = None
    terminal = False

    iterator = lines.__aiter__()
    while True:
        try:
            raw_line = await _await_with_cancel(
                lambda: anext(iterator),
                options.cancel_event,
            )
        except StopAsyncIteration:
            break

        line = raw_line.strip()
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            continue

        data_text = line[len("data:") :].strip()
        if not data_text or data_text == "[DONE]":
            continue
        try:
            event = json.loads(data_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid SSE data: {data_text[:200]}"
            ) from exc
        if not isinstance(event, dict):
            raise ValueError(f"Invalid SSE data: {data_text[:200]}")

        event_type = event.get("type")
        error = event.get("error")
        if event_type == "error" or error is not None:
            if isinstance(error, dict):
                message = error.get("message") or json.dumps(
                    error,
                    ensure_ascii=False,
                )
            else:
                message = str(error or event_type)
            raise RuntimeError(f"Provider stream error: {message}")

        if event_type == "message_start":
            message = event.get("message")
            if isinstance(message, dict):
                usage = message.get("usage")
                if isinstance(usage, dict):
                    _apply_usage(partial.usage, usage)
            continue

        if event_type == "content_block_start":
            wire_index = event.get("index")
            block = event.get("content_block")
            if not isinstance(wire_index, int) or not isinstance(block, dict):
                raise ValueError("Invalid content_block_start event")
            _start_block(
                wire_index,
                block,
                blocks,
                stream,
                partial,
            )
            continue

        if event_type == "content_block_delta":
            wire_index = event.get("index")
            if not isinstance(wire_index, int):
                raise ValueError("Invalid content_block_delta event")
            _handle_delta(
                wire_index,
                event,
                blocks,
                stream,
                partial,
            )
            continue

        if event_type == "content_block_stop":
            wire_index = event.get("index")
            if isinstance(wire_index, int):
                state = blocks.get(wire_index)
                if state is not None:
                    _close_block(state, stream, partial)
            continue

        if event_type == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, dict):
                reason = delta.get("stop_reason")
                if isinstance(reason, str):
                    stop_reason = reason
                details = delta.get("stop_details")
                if details is not None:
                    stop_details = details
            details = event.get("stop_details")
            if details is not None:
                stop_details = details
            usage = event.get("usage")
            if isinstance(usage, dict):
                _apply_usage(partial.usage, usage)
            continue

        if event_type == "message_stop":
            terminal = True
            break

        # ping and provider-specific extension events are ignored.

    if not terminal:
        raise RuntimeError("Stream ended without message_stop")

    for wire_index in sorted(blocks):
        _close_block(blocks[wire_index], stream, partial)

    if stop_reason is None:
        raise ValueError("Provider stop_reason: missing")
    if stop_reason == "refusal":
        detail = ""
        if stop_details is not None:
            detail = f" ({stop_details})"
        raise RuntimeError(f"Provider refusal{detail}")
    try:
        done_reason = _DONE_REASONS[stop_reason]
    except KeyError:
        raise ValueError(f"Provider stop_reason: {stop_reason}") from None

    partial.stop_reason = done_reason
    partial.timestamp = _now_ms()
    stream.push(DoneEvent(reason=done_reason, message=partial))


async def _consume_response(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    model: Model,
    options: StreamOptions,
    stream: AssistantMessageEventStream,
    attempt_state: _AttemptState,
) -> None:
    async with client.stream(
        "POST",
        url,
        json=payload,
        headers=headers,
    ) as response:
        if response.status_code >= 400:
            body = (await response.aread()).decode(
                "utf-8",
                errors="replace",
            )
            raise _ProviderHTTPError(
                response.status_code,
                body,
                response.headers,
            )
        await _consume_sse(
            response.aiter_lines(),
            stream,
            model=model,
            options=options,
            attempt_state=attempt_state,
        )


async def _run(
    model: Model,
    context: Context,
    options: StreamOptions,
    stream: AssistantMessageEventStream,
    *,
    api_key: str,
    base_url: str,
    headers: dict[str, str] | None,
    client: httpx.AsyncClient | None,
) -> None:
    try:
        url = _messages_url(base_url)
        request_headers = _request_headers(api_key, headers)
        payload = _build_payload(model, context, options)

        max_retries = max(0, options.max_retries)
        for retry_index in range(max_retries + 1):
            attempt_state = _AttemptState()

            async def consume_once() -> None:
                if client is not None:
                    await _consume_response(
                        client,
                        url,
                        payload,
                        request_headers,
                        model,
                        options,
                        stream,
                        attempt_state,
                    )
                    return

                timeout = httpx.Timeout(60.0, read=300.0)
                # Model requests connect directly; system proxy settings add latency.
                async with httpx.AsyncClient(
                    timeout=timeout,
                    trust_env=False,
                ) as owned:
                    await _consume_response(
                        owned,
                        url,
                        payload,
                        request_headers,
                        model,
                        options,
                        stream,
                        attempt_state,
                    )

            try:
                await _await_with_cancel(consume_once, options.cancel_event)
                return
            except _Aborted:
                stream.push(
                    ErrorEvent(
                        reason="aborted",
                        error=_error_message(
                            model,
                            "Request aborted",
                            aborted=True,
                            partial=attempt_state.partial,
                        ),
                    )
                )
                return
            except Exception as exc:
                can_retry = (
                    not attempt_state.started
                    and retry_index < max_retries
                    and _is_retryable_error(exc)
                )
                if not can_retry:
                    stream.push(
                        ErrorEvent(
                            reason="error",
                            error=_error_message(
                                model,
                                str(exc),
                                partial=attempt_state.partial,
                            ),
                        )
                    )
                    return

                delay = _retry_delay(retry_index, options, exc)
                try:
                    await _await_with_cancel(
                        lambda: asyncio.sleep(delay),
                        options.cancel_event,
                    )
                except _Aborted:
                    stream.push(
                        ErrorEvent(
                            reason="aborted",
                            error=_error_message(
                                model,
                                "Request aborted",
                                aborted=True,
                            ),
                        )
                    )
                    return
    except _Aborted:
        stream.push(
            ErrorEvent(
                reason="aborted",
                error=_error_message(
                    model,
                    "Request aborted",
                    aborted=True,
                ),
            )
        )
    except Exception as exc:
        stream.push(
            ErrorEvent(
                reason="error",
                error=_error_message(model, str(exc)),
            )
        )
    finally:
        stream.end()


def create_anthropic_messages_stream(
    *,
    api_key: str,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> StreamFunction:
    """Bind the adapter to one credential and optional HTTP client."""

    def stream(
        model: Model,
        context: Context,
        options: StreamOptions,
    ) -> AssistantMessageEventStream:
        result = AssistantMessageEventStream()
        resolved_base_url = model.base_url or base_url or DEFAULT_BASE_URL
        task = asyncio.create_task(
            _run(
                model,
                context,
                options,
                result,
                api_key=api_key,
                base_url=resolved_base_url,
                headers=headers,
                client=client,
            )
        )
        result.attach_task(task)
        return result

    return stream


__all__ = ["create_anthropic_messages_stream"]

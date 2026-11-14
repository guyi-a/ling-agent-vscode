"""OpenAI-compatible chat completions adapter."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, TypeVar

import httpx

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
    Message,
    Model,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

API_NAME = "openai-completions"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

_DONE_REASONS: dict[str, Literal["stop", "length", "tool_use"]] = {
    "stop": "stop",
    "end": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")
_T = TypeVar("_T")


class _Aborted(Exception):
    pass


class _ProviderHTTPError(Exception):
    def __init__(self, status: int, body: str, headers: httpx.Headers) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.headers = headers


@dataclass(slots=True)
class _StreamingToolCall:
    block: ToolCall
    content_index: int
    arguments_json: str = ""
    stream_index: int | None = None


@dataclass(slots=True)
class _AttemptState:
    started: bool = False
    partial: AssistantMessage | None = None


@dataclass(slots=True, frozen=True)
class _Compat:
    supports_developer_role: bool = False
    supports_reasoning_effort: bool = True
    supports_usage_in_streaming: bool = True
    supports_finish_reason: bool = True
    max_tokens_field: Literal["max_completion_tokens", "max_tokens"] = "max_tokens"
    requires_tool_result_name: bool = False
    requires_assistant_after_tool_result: bool = False
    supports_tool_choice: bool = True


def _resolve_compat(model: Model) -> _Compat:
    compat = model.compat or {}
    max_tokens_field = compat.get("max_tokens_field", "max_tokens")
    if max_tokens_field not in ("max_completion_tokens", "max_tokens"):
        raise ValueError(f"Unsupported max_tokens_field: {max_tokens_field!r}")
    return _Compat(
        supports_developer_role=bool(
            compat.get("supports_developer_role", False)
        ),
        supports_reasoning_effort=bool(
            compat.get("supports_reasoning_effort", True)
        ),
        supports_usage_in_streaming=bool(
            compat.get("supports_usage_in_streaming", True)
        ),
        supports_finish_reason=bool(compat.get("supports_finish_reason", True)),
        max_tokens_field=max_tokens_field,
        requires_tool_result_name=bool(
            compat.get("requires_tool_result_name", False)
        ),
        requires_assistant_after_tool_result=bool(
            compat.get("requires_assistant_after_tool_result", False)
        ),
        supports_tool_choice=bool(compat.get("supports_tool_choice", True)),
    )


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


def _map_finish_reason(reason: str) -> Literal["stop", "length", "tool_use"]:
    try:
        return _DONE_REASONS[reason]
    except KeyError:
        raise ValueError(f"Provider finish_reason: {reason}") from None


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, _ProviderHTTPError):
        should_retry = error.headers.get("x-should-retry")
        if should_retry == "true":
            return True
        if should_retry == "false":
            return False
        return error.status in (408, 409, 429) or error.status >= 500
    return isinstance(error, httpx.TransportError)


def _retry_delay(
    retry_index: int,
    options: StreamOptions,
    error: Exception,
) -> float:
    server_delay: float | None = None
    if isinstance(error, _ProviderHTTPError):
        retry_after_ms = error.headers.get("retry-after-ms")
        if retry_after_ms:
            try:
                server_delay = max(0.0, float(retry_after_ms) / 1000)
            except ValueError:
                pass
        retry_after = (
            error.headers.get("retry-after") if server_delay is None else None
        )
        if retry_after is not None:
            try:
                server_delay = max(0.0, float(retry_after))
            except ValueError:
                pass

    if server_delay is None:
        base_delay = max(0.0, options.retry_base_delay)
        delay = base_delay * 2**retry_index
    else:
        delay = server_delay
    return min(delay, max(0.0, options.retry_max_delay))


async def _await_with_cancel(
    factory: Callable[[], Awaitable[_T]],
    cancel_event: asyncio.Event | None,
) -> _T:
    if cancel_event is not None and cancel_event.is_set():
        raise _Aborted
    if cancel_event is None:
        return await factory()

    task = asyncio.create_task(factory())
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait(
        (task, cancel_task),
        return_when=asyncio.FIRST_COMPLETED,
    )
    if cancel_task in done:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        raise _Aborted

    cancel_task.cancel()
    with suppress(asyncio.CancelledError):
        await cancel_task
    return await task


def _update_usage(usage: Usage, data: dict[str, Any]) -> None:
    usage.input_tokens = int(data.get("prompt_tokens") or 0)
    usage.output_tokens = int(data.get("completion_tokens") or 0)
    usage.total_tokens = int(data.get("total_tokens") or 0)
    usage.cache_read_tokens = int(data.get("prompt_cache_hit_tokens") or 0)

    details = data.get("prompt_tokens_details")
    if isinstance(details, dict) and not usage.cache_read_tokens:
        usage.cache_read_tokens = int(details.get("cached_tokens") or 0)


def _user_content(
    content: str | list[TextContent | ImageContent],
) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content

    parts: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, TextContent):
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block.mime_type};base64,{block.data}",
                    },
                }
            )
    return parts


def _image_part(block: ImageContent) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{block.mime_type};base64,{block.data}",
        },
    }


def _tool_result_text(content: list[TextContent | ImageContent]) -> str:
    return "\n".join(block.text for block in content if isinstance(block, TextContent))


def _tool_result_messages(
    message: ToolResultMessage,
    compat: _Compat,
) -> list[dict[str, Any]]:
    text = _tool_result_text(message.content)
    images = [block for block in message.content if isinstance(block, ImageContent)]
    tool_content = text or ("(see attached image)" if images else "(no tool output)")
    tool_message: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": message.tool_call_id,
        "content": tool_content,
    }
    if compat.requires_tool_result_name:
        tool_message["name"] = message.tool_name
    messages: list[dict[str, Any]] = [
        tool_message
    ]
    if images:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Attached image(s) from tool result:"},
                    *(_image_part(block) for block in images),
                ],
            }
        )
    return messages


def _message_to_wire(message: Message, compat: _Compat) -> list[dict[str, Any]]:
    if isinstance(message, UserMessage):
        return [{"role": "user", "content": _user_content(message.content)}]

    if isinstance(message, AssistantMessage):
        text = "".join(
            block.text for block in message.content if isinstance(block, TextContent)
        )
        calls = [block for block in message.content if isinstance(block, ToolCall)]
        if not text and not calls:
            # Aborted or failed turns can leave an assistant message with no
            # content; providers reject those instead of ignoring them.
            return []
        wire: dict[str, Any] = {"role": "assistant", "content": text or None}
        thinking = "\n".join(
            block.thinking
            for block in message.content
            if isinstance(block, ThinkingContent)
        )
        reasoning_field = next(
            (
                block.signature
                for block in message.content
                if isinstance(block, ThinkingContent)
                and block.signature in _REASONING_FIELDS
            ),
            None,
        )
        if thinking and reasoning_field:
            wire[reasoning_field] = thinking
        if calls:
            wire["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
        return [wire]

    if isinstance(message, ToolResultMessage):
        return _tool_result_messages(message, compat)

    raise TypeError(f"Unsupported message type: {type(message)!r}")


def _tool_to_wire(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _parse_streaming_arguments(arguments_json: str) -> dict[str, Any]:
    """Expose partial arguments when the accumulated JSON is already valid."""
    if not arguments_json.strip():
        return {}
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _finish_tool_call(tool_call: _StreamingToolCall) -> None:
    try:
        arguments = json.loads(tool_call.arguments_json or "{}")
    except json.JSONDecodeError as exc:
        name = tool_call.block.name or "<unknown>"
        raise ValueError(f"Invalid JSON arguments for tool call {name!r}") from exc
    if not isinstance(arguments, dict):
        raise ValueError(
            f"Tool call {tool_call.block.name!r} arguments must be a JSON object"
        )
    tool_call.block.arguments = arguments


def _build_payload(
    model: Model,
    context: Context,
    options: StreamOptions,
) -> dict[str, Any]:
    compat = _resolve_compat(model)
    messages: list[dict[str, Any]] = []
    if context.system_prompt:
        role = "developer" if compat.supports_developer_role else "system"
        messages.append({"role": role, "content": context.system_prompt})
    last_role: str | None = None
    for message in context.messages:
        if (
            compat.requires_assistant_after_tool_result
            and last_role == "tool"
            and isinstance(message, UserMessage)
        ):
            messages.append(
                {
                    "role": "assistant",
                    "content": "I have processed the tool results.",
                }
            )
        wire_messages = _message_to_wire(message, compat)
        if not wire_messages:
            continue
        messages.extend(wire_messages)
        last_role = wire_messages[-1]["role"]

    payload: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
    }
    if compat.supports_usage_in_streaming:
        payload["stream_options"] = {"include_usage": True}
    if context.tools:
        payload["tools"] = [_tool_to_wire(tool) for tool in context.tools]
        if compat.supports_tool_choice:
            payload["tool_choice"] = "auto"
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    if options.max_tokens is not None:
        payload[compat.max_tokens_field] = options.max_tokens
    if options.reasoning is not None and compat.supports_reasoning_effort:
        payload["reasoning_effort"] = options.reasoning
    return payload


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

    compat = _resolve_compat(model)
    partial = _new_partial(model)
    if attempt_state is not None:
        attempt_state.started = True
        attempt_state.partial = partial
    stream.push(StartEvent(partial=partial))

    text_index: int | None = None
    thinking_index: int | None = None
    finish_reason: str | None = None
    tool_calls_by_index: dict[int, _StreamingToolCall] = {}
    tool_calls_by_id: dict[str, _StreamingToolCall] = {}
    tool_call_states: list[_StreamingToolCall] = []

    def close_thinking() -> None:
        nonlocal thinking_index
        if thinking_index is None:
            return
        block = partial.content[thinking_index]
        if isinstance(block, ThinkingContent):
            stream.push(
                ThinkingEndEvent(
                    content_index=thinking_index,
                    content=block.thinking,
                    partial=partial,
                )
            )
        thinking_index = None

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
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue

        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid SSE data: {data[:200]}") from exc

        error = chunk.get("error")
        if error is not None:
            if isinstance(error, dict):
                message = error.get("message") or json.dumps(
                    error, ensure_ascii=False
                )
            else:
                message = str(error)
            raise RuntimeError(f"Provider stream error: {message}")

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            _update_usage(partial.usage, usage)

        choices = chunk.get("choices") or []
        if not choices:
            continue

        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        delta = choice.get("delta") or {}

        content = delta.get("content")
        if content:
            close_thinking()
            if text_index is None:
                partial.content.append(TextContent(""))
                text_index = len(partial.content) - 1
                stream.push(TextStartEvent(content_index=text_index, partial=partial))

            block = partial.content[text_index]
            if not isinstance(block, TextContent):
                raise RuntimeError("Unexpected content block while streaming text")
            block.text += content
            stream.push(
                TextDeltaEvent(
                    content_index=text_index,
                    delta=content,
                    partial=partial,
                )
            )

        reasoning_field = next(
            (
                field
                for field in _REASONING_FIELDS
                if isinstance(delta.get(field), str) and delta[field]
            ),
            None,
        )
        if reasoning_field is not None:
            reasoning_delta = delta[reasoning_field]
            if thinking_index is None:
                partial.content.append(
                    ThinkingContent(thinking="", signature=reasoning_field)
                )
                thinking_index = len(partial.content) - 1
                stream.push(
                    ThinkingStartEvent(
                        content_index=thinking_index,
                        partial=partial,
                    )
                )

            block = partial.content[thinking_index]
            if not isinstance(block, ThinkingContent):
                raise RuntimeError("Unexpected content block while streaming thinking")
            block.thinking += reasoning_delta
            stream.push(
                ThinkingDeltaEvent(
                    content_index=thinking_index,
                    delta=reasoning_delta,
                    partial=partial,
                )
            )

        for tool_call_delta in delta.get("tool_calls") or []:
            close_thinking()
            stream_index = tool_call_delta.get("index")
            if not isinstance(stream_index, int):
                stream_index = None
            call_id = tool_call_delta.get("id")
            function = tool_call_delta.get("function") or {}
            name = function.get("name")

            tool_call = (
                tool_calls_by_index.get(stream_index)
                if stream_index is not None
                else None
            )
            if tool_call is None and call_id:
                tool_call = tool_calls_by_id.get(call_id)
            if tool_call is None:
                block = ToolCall(id=call_id or "", name=name or "", arguments={})
                tool_call = _StreamingToolCall(
                    block=block,
                    content_index=len(partial.content),
                    stream_index=stream_index,
                )
                partial.content.append(block)
                tool_call_states.append(tool_call)
                if stream_index is not None:
                    tool_calls_by_index[stream_index] = tool_call
                if call_id:
                    tool_calls_by_id[call_id] = tool_call
                stream.push(
                    ToolCallStartEvent(
                        content_index=tool_call.content_index,
                        partial=partial,
                    )
                )

            if stream_index is not None and tool_call.stream_index is None:
                tool_call.stream_index = stream_index
                tool_calls_by_index[stream_index] = tool_call
            if call_id:
                tool_call.block.id = call_id
                tool_calls_by_id[call_id] = tool_call
            if name:
                tool_call.block.name = name

            arguments_delta = function.get("arguments")
            if isinstance(arguments_delta, str) and arguments_delta:
                tool_call.arguments_json += arguments_delta
                tool_call.block.arguments = _parse_streaming_arguments(
                    tool_call.arguments_json
                )
                stream.push(
                    ToolCallDeltaEvent(
                        content_index=tool_call.content_index,
                        delta=arguments_delta,
                        partial=partial,
                    )
                )

    tool_call_by_block_id = {id(state.block): state for state in tool_call_states}
    for content_index, block in enumerate(partial.content):
        if content_index == text_index and isinstance(block, TextContent):
            stream.push(
                TextEndEvent(
                    content_index=content_index,
                    content=block.text,
                    partial=partial,
                )
            )
        elif content_index == thinking_index and isinstance(block, ThinkingContent):
            stream.push(
                ThinkingEndEvent(
                    content_index=content_index,
                    content=block.thinking,
                    partial=partial,
                )
            )
        elif isinstance(block, ToolCall):
            tool_call = tool_call_by_block_id[id(block)]
            _finish_tool_call(tool_call)
            stream.push(
                ToolCallEndEvent(
                    content_index=content_index,
                    tool_call=block,
                    partial=partial,
                )
            )

    if finish_reason is not None:
        try:
            done_reason = _map_finish_reason(finish_reason)
        except ValueError as exc:
            partial.stop_reason = "error"
            partial.error_message = str(exc)
            partial.timestamp = _now_ms()
            stream.push(ErrorEvent(reason="error", error=partial))
            return
    elif not compat.supports_finish_reason:
        done_reason = (
            "tool_use"
            if any(isinstance(block, ToolCall) for block in partial.content)
            else "stop"
        )
    else:
        partial.stop_reason = "error"
        partial.error_message = "Stream ended without finish_reason"
        partial.timestamp = _now_ms()
        stream.push(ErrorEvent(reason="error", error=partial))
        return
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
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
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
    url = f"{base_url.rstrip('/')}/chat/completions"
    request_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)
    payload = _build_payload(model, context, options)

    try:
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
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as owned:
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
                error=_error_message(model, "Request aborted", aborted=True),
            )
        )
    except Exception as exc:
        stream.push(ErrorEvent(reason="error", error=_error_message(model, str(exc))))
    finally:
        stream.end()


def create_openai_completions_stream(
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


__all__ = ["create_openai_completions_stream"]

"""OpenAI Responses API streaming adapter."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Literal

import httpx

from ._http import _Aborted, _AttemptState, _ProviderHTTPError, _await_with_cancel, _is_retryable_error, _retry_delay
from ..events import DoneEvent, ErrorEvent, StartEvent, TextDeltaEvent, TextEndEvent, TextStartEvent, ThinkingDeltaEvent, ThinkingEndEvent, ThinkingStartEvent, ToolCallDeltaEvent, ToolCallEndEvent, ToolCallStartEvent
from ..stream import AssistantMessageEventStream, StreamFunction, StreamOptions
from ..types import AssistantMessage, Context, ImageContent, Message, Model, TextContent, ThinkingContent, Tool, ToolCall, ToolResultMessage, Usage, UserMessage

API_NAME = "openai-responses"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _partial(model: Model) -> AssistantMessage:
    return AssistantMessage([], model.provider, model.id, Usage(), "pending", _now_ms(), API_NAME)


def _error(model: Model, message: str, *, aborted: bool = False, partial: AssistantMessage | None = None) -> AssistantMessage:
    return AssistantMessage(
        partial.content if partial else [], model.provider, model.id,
        partial.usage if partial else Usage(), "aborted" if aborted else "error",
        _now_ms(), API_NAME, message,
    )


def _content(value: str | list[TextContent | ImageContent]) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    result: list[dict[str, Any]] = []
    for block in value:
        if isinstance(block, TextContent):
            result.append({"type": "input_text", "text": block.text})
        elif isinstance(block, ImageContent):
            result.append({"type": "input_image", "image_url": f"data:{block.mime_type};base64,{block.data}"})
    return result


def _tool_output(message: ToolResultMessage) -> str:
    text = "\n".join(block.text for block in message.content if isinstance(block, TextContent))
    return text or "(no tool output)"


def _message_items(message: Message) -> list[dict[str, Any]]:
    if isinstance(message, UserMessage):
        return [{"role": "user", "content": _content(message.content)}]
    if isinstance(message, ToolResultMessage):
        return [{"type": "function_call_output", "call_id": message.tool_call_id, "output": _tool_output(message)}]
    items: list[dict[str, Any]] = []
    text = "".join(block.text for block in message.content if isinstance(block, TextContent))
    if text:
        items.append({"role": "assistant", "content": text})
    for block in message.content:
        if isinstance(block, ToolCall):
            items.append({"type": "function_call", "call_id": block.id, "name": block.name, "arguments": json.dumps(block.arguments, ensure_ascii=False)})
    return items


def _build_payload(model: Model, context: Context, options: StreamOptions) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model.id,
        "input": [item for message in context.messages for item in _message_items(message)],
        "stream": True,
    }
    if context.system_prompt:
        payload["instructions"] = context.system_prompt
    if context.tools:
        payload["tools"] = [{"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in context.tools]
    payload["reasoning"] = {"summary": "auto"}
    if options.reasoning is not None:
        payload["reasoning"]["effort"] = options.reasoning
    if options.max_tokens is not None:
        payload["max_output_tokens"] = options.max_tokens
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    return payload


def _update_usage(usage: Usage, value: dict[str, Any]) -> None:
    usage.input_tokens = int(value.get("input_tokens") or 0)
    usage.output_tokens = int(value.get("output_tokens") or 0)
    usage.total_tokens = int(value.get("total_tokens") or 0)
    details = value.get("input_tokens_details")
    if isinstance(details, dict):
        usage.cache_read_tokens = int(details.get("cached_tokens") or 0)


def _arguments(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _done_reason(response: dict[str, Any], has_tools: bool) -> Literal["stop", "length", "tool_use"]:
    if response.get("status") == "incomplete" and (response.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
        return "length"
    return "tool_use" if has_tools else "stop"


async def _consume_sse(lines: AsyncIterator[str], stream: AssistantMessageEventStream, *, model: Model, options: StreamOptions, attempt_state: _AttemptState) -> None:
    partial = _partial(model)
    attempt_state.started = True
    attempt_state.partial = partial
    stream.push(StartEvent(partial))
    text_index: int | None = None
    thinking_index: int | None = None
    tools: dict[str, tuple[ToolCall, int, str]] = {}
    response: dict[str, Any] = {}

    def close_thinking() -> None:
        nonlocal thinking_index
        if thinking_index is not None and isinstance(partial.content[thinking_index], ThinkingContent):
            block = partial.content[thinking_index]
            stream.push(ThinkingEndEvent(thinking_index, block.thinking, partial))
        thinking_index = None

    iterator = lines.__aiter__()
    while True:
        try:
            raw = await _await_with_cancel(lambda: anext(iterator), options.cancel_event)
        except StopAsyncIteration:
            break
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid SSE data: {data[:200]}") from exc
        if not isinstance(event, dict):
            continue
        if event.get("error") is not None:
            value = event["error"]
            raise RuntimeError(f"Provider stream error: {value.get('message') if isinstance(value, dict) else value}")
        kind = event.get("type", "")
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                close_thinking()
                if text_index is None:
                    partial.content.append(TextContent(""))
                    text_index = len(partial.content) - 1
                    stream.push(TextStartEvent(text_index, partial))
                block = partial.content[text_index]
                if isinstance(block, TextContent):
                    block.text += delta
                    stream.push(TextDeltaEvent(text_index, delta, partial))
        elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                if thinking_index is None:
                    partial.content.append(ThinkingContent("", signature="reasoning"))
                    thinking_index = len(partial.content) - 1
                    stream.push(ThinkingStartEvent(thinking_index, partial))
                block = partial.content[thinking_index]
                if isinstance(block, ThinkingContent):
                    block.thinking += delta
                    stream.push(ThinkingDeltaEvent(thinking_index, delta, partial))
        elif kind in {
            "response.reasoning_summary_text.done",
            "response.reasoning_text.done",
        }:
            close_thinking()
        elif kind == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                close_thinking()
                call_id = str(item.get("call_id") or item.get("id") or "")
                item_id = str(item.get("id") or call_id)
                block = ToolCall(call_id, str(item.get("name") or ""), {})
                index = len(partial.content)
                partial.content.append(block)
                tools[item_id] = (block, index, "")
                stream.push(ToolCallStartEvent(index, partial))
        elif kind == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id") or event.get("call_id") or "")
            state = tools.get(item_id)
            delta = event.get("delta")
            if state and isinstance(delta, str):
                block, index, value = state
                value += delta
                block.arguments = _arguments(value)
                tools[item_id] = (block, index, value)
                stream.push(ToolCallDeltaEvent(index, delta, partial))
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                item_id = str(item.get("id") or item.get("call_id") or "")
                state = tools.get(item_id)
                if state:
                    block, index, value = state
                    call_id = str(item.get("call_id") or "")
                    if call_id:
                        block.id = call_id
                    if isinstance(item.get("arguments"), str):
                        block.arguments = _arguments(item["arguments"])
                    stream.push(ToolCallEndEvent(index, block, partial))
        elif kind == "response.completed":
            value = event.get("response")
            if isinstance(value, dict):
                response = value
                if isinstance(value.get("usage"), dict):
                    _update_usage(partial.usage, value["usage"])

    close_thinking()
    if text_index is not None and isinstance(partial.content[text_index], TextContent):
        stream.push(TextEndEvent(text_index, partial.content[text_index].text, partial))
    if isinstance(response.get("usage"), dict):
        _update_usage(partial.usage, response["usage"])
    partial.stop_reason = _done_reason(response, any(isinstance(block, ToolCall) for block in partial.content))
    partial.timestamp = _now_ms()
    stream.push(DoneEvent(partial.stop_reason, partial))


async def _consume_response(client: httpx.AsyncClient, url: str, payload: dict[str, Any], headers: dict[str, str], model: Model, options: StreamOptions, stream: AssistantMessageEventStream, attempt_state: _AttemptState) -> None:
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            raise _ProviderHTTPError(response.status_code, body, response.headers)
        await _consume_sse(response.aiter_lines(), stream, model=model, options=options, attempt_state=attempt_state)


async def _run(model: Model, context: Context, options: StreamOptions, stream: AssistantMessageEventStream, *, api_key: str, base_url: str, headers: dict[str, str] | None, client: httpx.AsyncClient | None) -> None:
    url = f"{base_url.rstrip('/')}/responses"
    request_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    payload = _build_payload(model, context, options)
    try:
        for retry_index in range(max(0, options.max_retries) + 1):
            state = _AttemptState()

            async def consume() -> None:
                if client is not None:
                    await _consume_response(client, url, payload, request_headers, model, options, stream, state)
                else:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0), trust_env=False) as owned:
                        await _consume_response(owned, url, payload, request_headers, model, options, stream, state)

            try:
                await _await_with_cancel(consume, options.cancel_event)
                return
            except _Aborted:
                stream.push(ErrorEvent("aborted", _error(model, "Request aborted", aborted=True, partial=state.partial)))
                return
            except Exception as exc:
                if not (not state.started and retry_index < options.max_retries and _is_retryable_error(exc)):
                    stream.push(ErrorEvent("error", _error(model, str(exc), partial=state.partial)))
                    return
                await _await_with_cancel(lambda: asyncio.sleep(_retry_delay(retry_index, options, exc)), options.cancel_event)
    except _Aborted:
        stream.push(ErrorEvent("aborted", _error(model, "Request aborted", aborted=True)))
    except Exception as exc:
        stream.push(ErrorEvent("error", _error(model, str(exc))))
    finally:
        stream.end()


def create_openai_responses_stream(*, api_key: str, base_url: str | None = None, headers: dict[str, str] | None = None, client: httpx.AsyncClient | None = None) -> StreamFunction:
    """Bind the Responses adapter to one credential and optional HTTP client."""
    def stream(model: Model, context: Context, options: StreamOptions) -> AssistantMessageEventStream:
        result = AssistantMessageEventStream()
        task = asyncio.create_task(_run(model, context, options, result, api_key=api_key, base_url=model.base_url or base_url or DEFAULT_BASE_URL, headers=headers, client=client))
        result.attach_task(task)
        return result
    return stream


__all__ = ["create_openai_responses_stream"]

"""Convert context messages into compaction rows."""

from __future__ import annotations

import json
from typing import Sequence

from ling_ai.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from .types import CompactionRow


def rows_from_messages(messages: Sequence[Message]) -> list[CompactionRow]:
    rows: list[CompactionRow] = []
    for order, message in enumerate(messages):
        if isinstance(message, UserMessage):
            rows.append(
                CompactionRow(
                    id=f"user:{order}",
                    order=order,
                    role="user",
                    content=_user_text(message.content),
                )
            )
            continue

        if isinstance(message, AssistantMessage):
            rows.append(
                CompactionRow(
                    id=f"assistant:{order}",
                    order=order,
                    role="assistant",
                    content=_assistant_text(message.content),
                    reasoning=_assistant_reasoning(message.content),
                    tool_calls=_assistant_tool_calls(message.content),
                    total_tokens=(
                        message.usage.total_tokens
                        if message.usage.total_tokens > 0
                        else None
                    ),
                )
            )
            continue

        if isinstance(message, ToolResultMessage):
            call_id = message.tool_call_id or str(order)
            rows.append(
                CompactionRow(
                    id=f"tool:{call_id}",
                    order=order,
                    role="tool",
                    content=_tool_result_text(message.content),
                    tool_name=message.tool_name,
                )
            )
            continue

        raise TypeError(f"Unsupported message type: {type(message)!r}")

    return rows


def _user_text(content: object) -> str:
    if isinstance(content, str):
        return content
    return _content_text(content)


def _assistant_text(content: object) -> str:
    pieces: list[str] = []
    for block in _blocks(content):
        if isinstance(block, TextContent):
            pieces.append(block.text)
    return "\n".join(pieces)


def _assistant_reasoning(content: object) -> str:
    pieces: list[str] = []
    for block in _blocks(content):
        if isinstance(block, ThinkingContent):
            pieces.append(block.thinking)
    return "\n".join(pieces)


def _assistant_tool_calls(content: object) -> str:
    calls = [
        {
            "id": block.id,
            "name": block.name,
            "arguments": block.arguments,
        }
        for block in _blocks(content)
        if isinstance(block, ToolCall)
    ]
    if not calls:
        return ""
    return json.dumps(calls, ensure_ascii=False, separators=(",", ":"))


def _tool_result_text(content: object) -> str:
    return _content_text(content)


def _content_text(content: object) -> str:
    pieces: list[str] = []
    for block in _blocks(content):
        if isinstance(block, TextContent):
            pieces.append(block.text)
        elif isinstance(block, ImageContent):
            pieces.append(f"[image: {block.mime_type}]")
    return "\n".join(pieces)


def _blocks(content: object) -> list[object]:
    return content if isinstance(content, list) else []


__all__ = ["rows_from_messages"]

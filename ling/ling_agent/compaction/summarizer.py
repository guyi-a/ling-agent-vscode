"""Build summarizer messages and truncate large tool results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .config import CompactionConfig
from .prompt import (
    summary_instructions,
    trigger_prompt,
    wrap_prior_summary,
)
from .types import CompactionRow


@dataclass(slots=True, frozen=True)
class SummarizeChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


def build_summarizer_messages(
    folded: Sequence[CompactionRow],
    prior_summary: str,
    config: CompactionConfig,
) -> list[SummarizeChatMessage]:
    messages: list[SummarizeChatMessage] = [
        SummarizeChatMessage("system", summary_instructions),
    ]

    if prior_summary.strip():
        messages.append(
            SummarizeChatMessage(
                "user",
                wrap_prior_summary(prior_summary),
            )
        )

    for row in folded:
        if row.role == "user":
            content = row.content.strip()
            if content:
                messages.append(SummarizeChatMessage("user", content))
            continue

        if row.role == "assistant":
            content = row.content
            if row.reasoning:
                content = f"{content}\n[reasoning]\n{row.reasoning}"
            if row.tool_calls:
                content = (
                    f"{content}\n[tool calls] "
                    f"{truncate_text(row.tool_calls, config)}"
                )
            if content.strip():
                messages.append(
                    SummarizeChatMessage("assistant", content.strip())
                )
            continue

        name = row.tool_name or "tool"
        messages.append(
            SummarizeChatMessage(
                "user",
                f"[tool result: {name}]\n"
                f"{truncate_text(row.content, config)}",
            )
        )

    if len(messages) <= 1:
        return []

    messages.append(SummarizeChatMessage("user", trigger_prompt))
    return messages


def truncate_text(value: str, config: CompactionConfig) -> str:
    limit = config.tool_result_truncate_threshold_chars
    keep = config.tool_result_truncate_keep_chars
    if (
        limit <= 0
        or keep <= 0
        or len(value) <= limit
        or keep * 2 >= len(value)
    ):
        return value

    omitted = len(value) - keep * 2
    return (
        value[:keep]
        + f"\n\n[... {omitted} chars truncated for compaction ...]\n\n"
        + value[-keep:]
    )


__all__ = [
    "SummarizeChatMessage",
    "build_summarizer_messages",
    "truncate_text",
]

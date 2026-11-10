"""Tests for compaction primitives."""

from __future__ import annotations

import asyncio

from ling_ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from ling_agent.compaction import (
    CompactionConfig,
    CompactionRow,
    active_rows,
    build_summarizer_messages,
    estimate_tokens,
    maybe_compact,
    project_model_messages,
    rows_from_messages,
    split,
    threshold_tokens,
    wrap_summary,
)
from ling_agent.compaction.types import CompactionRecord


def _user(text: str, timestamp: int = 1) -> UserMessage:
    return UserMessage(content=text, timestamp=timestamp)


def _assistant(
    blocks: list[object],
    *,
    total_tokens: int = 0,
    timestamp: int = 2,
) -> AssistantMessage:
    return AssistantMessage(
        content=blocks,  # type: ignore[arg-type]
        provider="test",
        model="test",
        usage=Usage(total_tokens=total_tokens),
        stop_reason="stop",
        timestamp=timestamp,
    )


def _tool(
    text: str,
    *,
    call_id: str = "call-1",
    timestamp: int = 3,
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name="read_file",
        content=[TextContent(text)],
        is_error=False,
        timestamp=timestamp,
    )


def _row(
    row_id: str,
    role: str,
    content: str,
    *,
    order: int = 0,
    total_tokens: int | None = None,
) -> CompactionRow:
    return CompactionRow(
        id=row_id,
        order=order,
        role=role,  # type: ignore[arg-type]
        content=content,
        total_tokens=total_tokens,
    )


def test_threshold_uses_reserves() -> None:
    config = CompactionConfig()
    assert threshold_tokens(config) == 848_000


def test_rows_from_messages() -> None:
    messages = [
        _user("hello"),
        _assistant(
            [
                TextContent("answer"),
                ThinkingContent("plan"),
                ToolCall(
                    id="call-1",
                    name="read_file",
                    arguments={"path": "a.txt"},
                ),
            ],
            total_tokens=42,
        ),
        _tool("result"),
    ]

    rows = rows_from_messages(messages)

    assert [row.id for row in rows] == [
        "user:0",
        "assistant:1",
        "tool:call-1",
    ]
    assert rows[1].content == "answer"
    assert rows[1].reasoning == "plan"
    assert "read_file" in rows[1].tool_calls
    assert rows[1].total_tokens == 42
    assert rows[2].content == "result"
    assert rows[2].tool_name == "read_file"


def test_rows_mark_images() -> None:
    rows = rows_from_messages(
        [
            UserMessage(
                content=[
                    TextContent("look"),
                    ImageContent(data="abc", mime_type="image/png"),
                ],
                timestamp=1,
            )
        ]
    )
    assert rows[0].content == "look\n[image: image/png]"


def test_estimate_uses_last_usage_anchor() -> None:
    rows = [
        _row("user:0", "user", "x" * 400, order=0),
        _row("assistant:1", "assistant", "done", order=1, total_tokens=5000),
        _row("user:2", "user", "y" * 400, order=2),
    ]

    assert estimate_tokens(rows, None, CompactionConfig()) == 5100


def test_active_rows_drops_folded_prefix() -> None:
    rows = [
        _row("user:0", "user", "a", order=0),
        _row("assistant:1", "assistant", "b", order=1),
        _row("user:2", "user", "c", order=2),
        _row("assistant:3", "assistant", "d", order=3),
    ]
    active = CompactionRecord(
        id="c1",
        through_message_id="assistant:1",
        summary="old",
        replaced_message_ids=("user:0", "assistant:1"),
        replaced_count=2,
        estimated_tokens=10,
    )

    assert [row.id for row in active_rows(rows, active)] == [
        "user:2",
        "assistant:3",
    ]


def test_split_keeps_last_user_turn() -> None:
    rows = [
        _row("user:0", "user", "old request", order=0),
        _row("assistant:1", "assistant", "old answer", order=1),
        _row("tool:call-1", "tool", "old result", order=2),
        _row("user:3", "user", "current request", order=3),
        _row("assistant:4", "assistant", "current answer", order=4),
    ]

    plan = split(rows, None, 1)

    assert plan is not None
    assert [row.id for row in plan.folded] == [
        "user:0",
        "assistant:1",
        "tool:call-1",
    ]
    assert plan.through_message_id == "tool:call-1"


def test_split_keeps_last_five_user_turns() -> None:
    rows: list[CompactionRow] = []
    for turn in range(7):
        rows.append(
            _row(
                f"user:{turn * 2}",
                "user",
                f"question {turn}",
                order=turn * 2,
            )
        )
        rows.append(
            _row(
                f"assistant:{turn * 2 + 1}",
                "assistant",
                f"answer {turn}",
                order=turn * 2 + 1,
            )
        )

    plan = split(rows, None, 5)

    assert plan is not None
    assert [row.id for row in plan.folded] == [
        "user:0",
        "assistant:1",
        "user:2",
        "assistant:3",
    ]
    assert plan.through_message_id == "assistant:3"


def test_split_requires_assistant_or_tool_content() -> None:
    rows = [
        _row("user:0", "user", "a", order=0),
        _row("user:1", "user", "b", order=1),
    ]

    assert split(rows, None, 1) is None


def test_maybe_compact_below_threshold() -> None:
    rows = [
        _row("user:0", "user", "hello", order=0),
        _row("assistant:1", "assistant", "done", order=1),
    ]
    called = False

    async def summarize(folded: object, prior: str) -> str:
        nonlocal called
        called = True
        return "summary"

    result = asyncio.run(
        maybe_compact(rows, None, CompactionConfig(), summarize)
    )

    assert result is None
    assert not called


def test_maybe_compact_creates_record() -> None:
    config = CompactionConfig(
        window_nominal_tokens=1_000,
        window_usable_ratio=1.0,
        reserved_output_tokens=0,
        buffer_tokens=0,
        keep_last_user_turns=1,
    )
    rows = [
        _row("user:0", "user", "old request", order=0),
        _row(
            "assistant:1",
            "assistant",
            "old answer",
            order=1,
            total_tokens=5000,
        ),
        _row("tool:call-1", "tool", "old result", order=2),
        _row("user:3", "user", "current request", order=3),
        _row("assistant:4", "assistant", "current answer", order=4),
    ]

    async def summarize(folded: object, prior: str) -> str:
        return "## 1. User Intent\nold request"

    result = asyncio.run(
        maybe_compact(rows, None, config, summarize)
    )

    assert result is not None
    assert result.record.through_message_id == "tool:call-1"
    assert result.record.replaced_count == 3
    assert result.record.replaced_message_ids == (
        "user:0",
        "assistant:1",
        "tool:call-1",
    )
    assert "compacted-summary" in result.summary_message.content


def test_maybe_compact_returns_none_on_summary_failure() -> None:
    config = CompactionConfig(
        window_nominal_tokens=1_000,
        window_usable_ratio=1.0,
        reserved_output_tokens=0,
        buffer_tokens=0,
        keep_last_user_turns=1,
    )
    rows = [
        _row("user:0", "user", "old request", order=0),
        _row(
            "assistant:1",
            "assistant",
            "old answer",
            order=1,
            total_tokens=5000,
        ),
        _row("user:2", "user", "current request", order=2),
    ]

    async def summarize(folded: object, prior: str) -> str:
        raise RuntimeError("summary failed")

    result = asyncio.run(
        maybe_compact(rows, None, config, summarize)
    )

    assert result is None


def test_build_summarizer_messages_includes_prior_and_truncates() -> None:
    config = CompactionConfig(
        tool_result_truncate_threshold_chars=20,
        tool_result_truncate_keep_chars=5,
    )
    folded = [
        _row("user:0", "user", "do work", order=0),
        _row("assistant:1", "assistant", "working", order=1),
        _row("tool:call-1", "tool", "z" * 100, order=2),
    ]

    messages = build_summarizer_messages(folded, "prior facts", config)

    assert messages[0].role == "system"
    assert "<prior-summary>" in messages[1].content
    assert any("<compact-control>" in message.content for message in messages)
    assert any(
        "truncated for compaction" in message.content
        for message in messages
    )


def test_wrap_summary_has_boundary_marker() -> None:
    text = wrap_summary("abc", "## 1. User Intent\nstuff")
    assert '<compacted-summary id="abc">' in text
    assert "Do NOT restart the task." in text


def test_project_model_messages() -> None:
    messages = [
        _user("old"),
        _assistant([TextContent("old answer")]),
        _user("new"),
    ]
    record = CompactionRecord(
        id="c1",
        through_message_id="assistant:1",
        summary="summary",
        replaced_message_ids=("user:0", "assistant:1"),
        replaced_count=2,
        estimated_tokens=10,
    )

    view = project_model_messages(messages, record)

    assert len(view) == 2
    assert isinstance(view[0], UserMessage)
    assert "compacted-summary" in view[0].content
    assert isinstance(view[1], UserMessage)
    assert view[1].content == "new"

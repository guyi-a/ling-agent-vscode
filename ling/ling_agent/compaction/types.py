"""Types shared by the context compaction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Sequence, TypeAlias

from ling_ai.types import UserMessage


@dataclass(slots=True, frozen=True)
class CompactionRow:
    id: str
    order: int
    role: Literal["user", "assistant", "tool"]
    content: str
    reasoning: str = ""
    tool_calls: str = ""
    tool_name: str = ""
    total_tokens: int | None = None


@dataclass(slots=True, frozen=True)
class CompactionRecord:
    id: str
    through_message_id: str
    summary: str
    replaced_message_ids: tuple[str, ...]
    replaced_count: int
    estimated_tokens: int


@dataclass(slots=True, frozen=True)
class CompactionPlan:
    folded: tuple[CompactionRow, ...]
    through_message_id: str
    prior_summary: str


SummarizeFn: TypeAlias = Callable[
    [Sequence[CompactionRow], str],
    Awaitable[str] | str,
]


@dataclass(slots=True, frozen=True)
class MaybeCompactResult:
    record: CompactionRecord
    summary_message: UserMessage


__all__ = [
    "CompactionPlan",
    "CompactionRecord",
    "CompactionRow",
    "MaybeCompactResult",
    "SummarizeFn",
]

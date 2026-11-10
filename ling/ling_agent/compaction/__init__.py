"""Context compaction primitives for the coding-agent harness."""

from .config import CompactionConfig, chars_per_token, threshold_tokens
from .engine import maybe_compact
from .estimate import active_rows, estimate_tokens
from .project import project_model_messages
from .prompt import (
    summary_instructions,
    trigger_prompt,
    wrap_prior_summary,
    wrap_summary,
)
from .rows import rows_from_messages
from .split import split
from .summarizer import (
    SummarizeChatMessage,
    build_summarizer_messages,
    truncate_text,
)
from .types import (
    CompactionPlan,
    CompactionRecord,
    CompactionRow,
    MaybeCompactResult,
    SummarizeFn,
)

__all__ = [
    "CompactionConfig",
    "CompactionPlan",
    "CompactionRecord",
    "CompactionRow",
    "MaybeCompactResult",
    "SummarizeChatMessage",
    "SummarizeFn",
    "active_rows",
    "build_summarizer_messages",
    "chars_per_token",
    "estimate_tokens",
    "maybe_compact",
    "project_model_messages",
    "rows_from_messages",
    "split",
    "summary_instructions",
    "threshold_tokens",
    "trigger_prompt",
    "truncate_text",
    "wrap_prior_summary",
    "wrap_summary",
]

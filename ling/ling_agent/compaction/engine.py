"""Pure compaction decision and record creation."""

from __future__ import annotations

import inspect
import time
import uuid
from typing import Sequence

from ling_ai.types import UserMessage

from .config import CompactionConfig, threshold_tokens
from .estimate import estimate_tokens
from .prompt import wrap_summary
from .split import split
from .types import (
    CompactionRecord,
    CompactionRow,
    MaybeCompactResult,
    SummarizeFn,
)


async def maybe_compact(
    rows: Sequence[CompactionRow],
    active: CompactionRecord | None,
    config: CompactionConfig,
    summarize: SummarizeFn,
) -> MaybeCompactResult | None:
    if not config.enabled or not rows:
        return None

    estimated = estimate_tokens(rows, active, config)
    if estimated < threshold_tokens(config):
        return None

    plan = split(rows, active, config.keep_last_user_turns)
    if plan is None:
        return None

    try:
        summary = summarize(plan.folded, plan.prior_summary)
        if inspect.isawaitable(summary):
            summary = await summary
    except Exception:
        return None

    if not isinstance(summary, str) or not summary.strip():
        return None

    record = CompactionRecord(
        id=uuid.uuid4().hex,
        through_message_id=plan.through_message_id,
        summary=summary.strip(),
        replaced_message_ids=tuple(row.id for row in plan.folded),
        replaced_count=len(plan.folded),
        estimated_tokens=estimated,
    )
    return MaybeCompactResult(
        record=record,
        summary_message=UserMessage(
            content=wrap_summary(record.id, record.summary),
            timestamp=int(time.time() * 1000),
        ),
    )


__all__ = ["maybe_compact"]

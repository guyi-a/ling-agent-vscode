"""Project full history into the compacted model view."""

from __future__ import annotations

import time
from typing import Sequence

from ling_ai.types import Message, UserMessage

from .prompt import wrap_summary
from .rows import rows_from_messages
from .types import CompactionRecord


def project_model_messages(
    messages: Sequence[Message],
    active: CompactionRecord | None,
    summary_message: UserMessage | None = None,
) -> list[Message]:
    if active is None:
        return list(messages)

    if summary_message is None:
        summary_message = UserMessage(
            content=wrap_summary(active.id, active.summary),
            timestamp=int(time.time() * 1000),
        )

    rows = rows_from_messages(messages)
    replaced = set(active.replaced_message_ids)
    kept = [
        message
        for row, message in zip(rows, messages)
        if row.id not in replaced
    ]
    return [summary_message, *kept]


__all__ = ["project_model_messages"]

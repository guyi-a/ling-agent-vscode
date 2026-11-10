"""Rough token estimate for compaction decisions."""

from __future__ import annotations

import re
from typing import Sequence

from .config import CompactionConfig, chars_per_token
from .types import CompactionRecord, CompactionRow

_IMAGE_MARKER = re.compile(r"\[image:\s*[^\]]+\]")
_IMAGE_TOKENS = 384


def active_rows(
    rows: Sequence[CompactionRow],
    active: CompactionRecord | None,
) -> list[CompactionRow]:
    if active is None:
        return list(rows)

    for index, row in enumerate(rows):
        if row.id == active.through_message_id:
            return list(rows[index + 1 :])
    return list(rows)


def estimate_tokens(
    rows: Sequence[CompactionRow],
    active: CompactionRecord | None,
    config: CompactionConfig,
) -> int:
    scope = active_rows(rows, active)
    cpt = chars_per_token(config)

    anchor_index = -1
    for index in range(len(scope) - 1, -1, -1):
        tokens = scope[index].total_tokens
        if tokens is not None and tokens > 0:
            anchor_index = index
            break

    if anchor_index >= 0:
        anchor = scope[anchor_index]
        assert anchor.total_tokens is not None
        return anchor.total_tokens + _rough_tokens(
            scope[anchor_index + 1 :],
            cpt,
        )

    total = _rough_tokens(scope, cpt)
    if active is not None and active.summary:
        total += len(active.summary) // cpt
    return total


def _rough_tokens(rows: Sequence[CompactionRow], cpt: int) -> int:
    chars = 0
    image_tokens = 0
    for row in rows:
        chars += len(row.content) + len(row.reasoning) + len(row.tool_calls)
        image_tokens += len(_IMAGE_MARKER.findall(row.content)) * _IMAGE_TOKENS
    return chars // cpt + image_tokens


__all__ = ["active_rows", "estimate_tokens"]

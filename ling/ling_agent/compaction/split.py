"""Choose the history prefix that should be compacted."""

from __future__ import annotations

from typing import Sequence

from .estimate import active_rows
from .types import CompactionPlan, CompactionRecord, CompactionRow


def split(
    rows: Sequence[CompactionRow],
    active: CompactionRecord | None,
    keep_last_user_turns: int,
) -> CompactionPlan | None:
    scope = active_rows(rows, active)
    if not scope:
        return None

    if keep_last_user_turns > 0:
        user_indexes = [
            index
            for index, row in enumerate(scope)
            if row.role == "user"
        ]
        if len(user_indexes) <= keep_last_user_turns:
            return None
        cut = user_indexes[-keep_last_user_turns]
    else:
        cut = len(scope)

    if cut <= 0:
        return None

    folded = scope[:cut]
    if not any(row.role in ("assistant", "tool") for row in folded):
        return None

    return CompactionPlan(
        folded=tuple(folded),
        through_message_id=folded[-1].id,
        prior_summary=active.summary if active is not None else "",
    )


__all__ = ["split"]

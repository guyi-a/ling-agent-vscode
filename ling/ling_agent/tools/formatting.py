"""Shared formatting helpers for tool output."""

from __future__ import annotations


def join_limited(lines: list[str], limit: int, empty: str) -> str:
    if not lines:
        return empty

    selected: list[str] = []
    total = 0
    for line in lines:
        cost = len(line) + 1
        if total + cost > limit:
            selected.append("[truncated]")
            break
        selected.append(line)
        total += cost
    return "\n".join(selected)


__all__ = ["join_limited"]

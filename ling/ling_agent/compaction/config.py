"""Compaction configuration and threshold calculation."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(slots=True, frozen=True)
class CompactionConfig:
    enabled: bool = True
    window_nominal_tokens: int = 1_000_000
    window_usable_ratio: float = 0.9
    reserved_output_tokens: int = 32_000
    buffer_tokens: int = 20_000
    keep_last_user_turns: int = 5
    chars_per_token: int = 4
    summarizer_max_tokens: int = 4_096
    summarizer_timeout_seconds: float = 120.0
    tool_result_truncate_threshold_chars: int = 48_000
    tool_result_truncate_keep_chars: int = 8_000

    def __post_init__(self) -> None:
        if self.window_nominal_tokens < 1:
            raise ValueError("window_nominal_tokens must be at least 1")
        if not 0 < self.window_usable_ratio <= 1:
            raise ValueError("window_usable_ratio must be in (0, 1]")
        if self.reserved_output_tokens < 0:
            raise ValueError("reserved_output_tokens must not be negative")
        if self.buffer_tokens < 0:
            raise ValueError("buffer_tokens must not be negative")
        if self.keep_last_user_turns < 0:
            raise ValueError("keep_last_user_turns must not be negative")
        if self.chars_per_token < 1:
            raise ValueError("chars_per_token must be at least 1")
        if self.summarizer_max_tokens < 1:
            raise ValueError("summarizer_max_tokens must be at least 1")
        if self.summarizer_timeout_seconds <= 0:
            raise ValueError("summarizer_timeout_seconds must be positive")


def threshold_tokens(config: CompactionConfig) -> int:
    raw = (
        floor(config.window_nominal_tokens * config.window_usable_ratio)
        - config.reserved_output_tokens
        - config.buffer_tokens
    )
    return max(raw, 1024)


def chars_per_token(config: CompactionConfig) -> int:
    return (
        config.chars_per_token
        if config.chars_per_token > 0
        else 4
    )


__all__ = ["CompactionConfig", "chars_per_token", "threshold_tokens"]

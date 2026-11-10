"""User-quoted selections sent alongside a prompt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SelectionKind = Literal["editor", "terminal", "chat"]

MAX_USER_SELECTIONS = 4
MAX_SELECTION_CHARS = 50_000


@dataclass(slots=True, frozen=True)
class UserSelection:
    kind: SelectionKind
    label: str
    text: str
    line_count: int
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("editor", "terminal", "chat"):
            raise ValueError(
                "selection kind must be 'editor', 'terminal', or 'chat'"
            )
        if not self.label:
            raise ValueError("selection label must not be empty")
        if not self.text.strip():
            raise ValueError("selection text must not be empty")
        if len(self.text) > MAX_SELECTION_CHARS:
            raise ValueError(
                f"selection text exceeds {MAX_SELECTION_CHARS} characters"
            )
        if self.line_count < 1:
            raise ValueError("selection line_count must be at least 1")
        if self.path is not None and not isinstance(self.path, str):
            raise ValueError("selection path must be a string")
        for name, value in (
            ("start_line", self.start_line),
            ("end_line", self.end_line),
        ):
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
            ):
                raise ValueError(f"selection {name} must be a positive integer")


__all__ = [
    "MAX_SELECTION_CHARS",
    "MAX_USER_SELECTIONS",
    "SelectionKind",
    "UserSelection",
]

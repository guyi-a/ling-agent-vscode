"""Track files read in the current agent session before allowing edits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ling_core import PreToolUse, PreToolUseDecision

from .workspace import Workspace, WorkspaceError


@dataclass(slots=True, frozen=True)
class FileStamp:
    mtime_ns: int
    size: int


class FileReadTracker:
    """Remember content stamps for files the model has seen.

    A successful read records the file's stamp. A successful write updates it,
    so repeated edits within one session do not require re-reading. An
    external change invalidates the stamp and asks the model to read again.
    """

    def __init__(self) -> None:
        self._states: dict[str, FileStamp] = {}

    def mark_read(self, path: str | Path) -> None:
        self._remember(path)

    def mark_written(self, path: str | Path) -> None:
        self._remember(path)

    def check_editable(
        self,
        path: str | Path,
        *,
        label: str | None = None,
    ) -> str | None:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return None
        stamp = _stamp(resolved)
        if stamp is None:
            return None
        name = label or str(resolved)
        known = self._states.get(_key(resolved))
        if known is None:
            return (
                f"File has not been read in this session: {name}. "
                "Use read_file before editing it."
            )
        if known != stamp:
            return (
                f"File changed on disk since it was read: {name}. "
                "Read it again before editing."
            )
        return None

    def clear(self) -> None:
        self._states.clear()

    def _remember(self, path: str | Path) -> None:
        resolved = Path(path).expanduser().resolve()
        stamp = _stamp(resolved)
        if stamp is not None:
            self._states[_key(resolved)] = stamp


def create_read_guard_hook(
    workspace: Workspace,
    tracker: FileReadTracker | None,
):
    """PreToolUse hook that blocks blind edits before approval is requested."""

    def read_guard(event: PreToolUse) -> PreToolUseDecision | None:
        if tracker is None:
            return None
        if event.call.name not in ("write_file", "edit_file"):
            return None
        raw_path = event.call.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        try:
            resolved = workspace.resolve(raw_path)
        except WorkspaceError:
            return None
        if event.call.name == "write_file" and not resolved.exists():
            return None
        issue = tracker.check_editable(resolved, label=raw_path)
        if issue is None:
            return None
        return PreToolUseDecision(allowed=False, reason=issue)

    return read_guard


def _stamp(path: Path) -> FileStamp | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return FileStamp(mtime_ns=stat.st_mtime_ns, size=stat.st_size)


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


__all__ = [
    "FileReadTracker",
    "FileStamp",
    "create_read_guard_hook",
]

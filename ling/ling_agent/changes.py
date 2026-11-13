"""Per-run workspace change recording and safe reversible application."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

MUTATION_TOOL_NAMES = frozenset({"write_file", "edit_file"})
MAX_RUN_CHANGE_BYTES = 1_000_000


class ChangeError(RuntimeError):
    pass


class ChangeConflictError(ChangeError):
    pass


@dataclass(slots=True, frozen=True)
class RunChange:
    """The net before/after state of one file within a single run."""

    path: str
    before_exists: bool
    before: str | None
    after_exists: bool
    after: str | None
    added: int
    removed: int


@dataclass(slots=True)
class PendingRunChange:
    """Mutable baseline/final pair collected while a run is active."""

    path: str
    before_exists: bool
    before: str | None
    after_exists: bool | None = None
    after: str | None = None


def normalize_change_path(root: Path, raw_path: object) -> str | None:
    """Return a safe workspace-relative POSIX path for a mutation target."""

    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path_text = raw_path.replace("\\", "/")
    if path_text == ".ling":
        path_text = ".agents"
    elif path_text.startswith(".ling/"):
        path_text = ".agents/" + path_text[len(".ling/") :]
    candidate = Path(path_text)
    if candidate.is_absolute():
        return None
    try:
        resolved_root = root.resolve()
        resolved = (resolved_root / candidate).resolve()
        return resolved.relative_to(resolved_root).as_posix()
    except (OSError, ValueError):
        return None


def read_change_state(
    root: Path,
    relative_path: str,
) -> tuple[bool, str | None]:
    """Read a text state used by the change ledger.

    The boolean reports whether the target exists. ``None`` text means the
    file could not be captured and therefore cannot be reverted safely.
    """

    path = _resolve_relative(root, relative_path)
    try:
        if not path.exists():
            return False, None
        if not path.is_file() or path.stat().st_size > MAX_RUN_CHANGE_BYTES:
            return True, None
        data = path.read_bytes()
        if b"\x00" in data:
            return True, None
        return True, data.decode("utf-8")
    except FileNotFoundError:
        return False, None
    except (OSError, UnicodeDecodeError):
        return True, None


def finalize_run_change(
    pending: PendingRunChange,
) -> RunChange | None:
    """Compute one net file change, or ``None`` when it is unchanged."""

    if pending.after_exists is None:
        return None
    if pending.before_exists and pending.before is None:
        return None
    if pending.after_exists and pending.after is None:
        return None
    if (
        pending.before_exists == pending.after_exists
        and pending.before == pending.after
    ):
        return None

    before = pending.before if pending.before_exists else ""
    after = pending.after if pending.after_exists else ""
    added, removed = _line_diff_stats(before or "", after or "")
    return RunChange(
        path=pending.path,
        before_exists=pending.before_exists,
        before=pending.before,
        after_exists=pending.after_exists,
        after=pending.after,
        added=added,
        removed=removed,
    )


def revert_run_changes(root: Path, changes: tuple[RunChange, ...]) -> int:
    """Restore all files if the workspace still matches the recorded output."""

    resolved_root = root.resolve()
    for change in changes:
        current_exists, current = read_change_state(
            resolved_root,
            change.path,
        )
        if current_exists != change.after_exists or current != change.after:
            raise ChangeConflictError(
                f"{change.path} changed after this run; undo was not applied."
            )

    for change in changes:
        target = _resolve_relative(resolved_root, change.path)
        if not change.before_exists:
            if target.exists():
                target.unlink()
            continue
        if change.before is None:
            raise ChangeConflictError(
                f"{change.path} has no recoverable before state."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(change.before.encode("utf-8"))
    return len(changes)


def _resolve_relative(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise ChangeError(
            f"Change path escapes the workspace: {relative_path}"
        ) from None
    return candidate


def _line_diff_stats(before: str, after: str) -> tuple[int, int]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    added = 0
    removed = 0
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation == "insert":
            added += new_end - new_start
        elif operation == "delete":
            removed += old_end - old_start
        elif operation == "replace":
            removed += old_end - old_start
            added += new_end - new_start
    return added, removed


__all__ = [
    "ChangeConflictError",
    "ChangeError",
    "MAX_RUN_CHANGE_BYTES",
    "MUTATION_TOOL_NAMES",
    "PendingRunChange",
    "RunChange",
    "finalize_run_change",
    "normalize_change_path",
    "read_change_state",
    "revert_run_changes",
]

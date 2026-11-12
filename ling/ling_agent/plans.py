"""Persist implementation plans as markdown files in the workspace."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PLANS_DIRECTORY = ".agents/plans"
MAX_PLAN_CHARS = 100_000
MAX_SLUG_CHARS = 40

_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


class PlanStore:
    """Write plans under ``<workspace>/.agents/plans``."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.root = self.workspace_root / PLANS_DIRECTORY
        self._current: Path | None = None
        self._created_at: str | None = None
        self._session_id: str | None = None
        self._run_id: str | None = None

    def bind(self, *, session_id: str | None, run_id: str | None) -> None:
        self._session_id = session_id
        self._run_id = run_id

    @property
    def current(self) -> Path | None:
        return self._current

    def create(self, markdown: str) -> Path:
        text = _normalize(markdown)
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}-{_slug(text)}"
        path = self.root / f"{base}.md"
        counter = 2
        while path.exists():
            path = self.root / f"{base}-{counter}.md"
            counter += 1
        self._current = path
        self._created_at = _now()
        self._write(path, text)
        return path

    def update(self, markdown: str) -> Path:
        text = _normalize(markdown)
        if self._current is None:
            return self.create(text)
        self._write(self._current, text)
        return self._current

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.workspace_root).as_posix()

    def resolve(self, relative_path: str) -> Path:
        """Resolve a workspace-relative plan path, rejecting escapes."""
        candidate = (self.workspace_root / relative_path).resolve()
        plans_root = self.root.resolve()
        if candidate == plans_root or plans_root not in candidate.parents:
            raise ValueError("plan path must be inside .agents/plans")
        return candidate

    def _write(self, path: Path, markdown: str) -> None:
        created = self._created_at or _now()
        lines = [
            "---",
            "status: ready",
            f"created: {created}",
            f"updated: {_now()}",
        ]
        if self._session_id:
            lines.append(f"session: {self._session_id}")
        if self._run_id:
            lines.append(f"run: {self._run_id}")
        lines.extend(["---", "", markdown.rstrip(), ""])
        path.write_text("\n".join(lines), encoding="utf-8")


def _normalize(markdown: str) -> str:
    if not isinstance(markdown, str):
        raise ValueError("plan must be a string")
    text = markdown.strip()
    if not text:
        raise ValueError("plan must not be empty")
    if len(text) > MAX_PLAN_CHARS:
        raise ValueError(
            f"plan must be at most {MAX_PLAN_CHARS} characters"
        )
    return text


def _slug(markdown: str) -> str:
    match = _HEADING.search(markdown)
    if match is not None:
        title = match.group(1)
    else:
        title = next(
            (line for line in markdown.splitlines() if line.strip()),
            "plan",
        )
    slug = _SLUG_CHARS.sub("-", title.lower()).strip("-")
    return slug[:MAX_SLUG_CHARS].strip("-") or "plan"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "MAX_PLAN_CHARS",
    "PLANS_DIRECTORY",
    "PlanStore",
]

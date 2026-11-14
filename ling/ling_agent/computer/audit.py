"""JSONL audit log for computer use.

Input text and values are never written. Callers pass lengths and action
metadata instead, so a password typed into a field cannot leak into the
audit trail.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..paths import default_state_root

_AUDIT_FILE = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.jsonl$")


class AuditLogger:
    """Append computer-use events to one JSONL file per day."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        root: str | Path | None = None,
        retention_days: int = 30,
    ) -> None:
        self._enabled = enabled
        self._root = (
            Path(root)
            if root is not None and str(root).strip()
            else default_state_root() / "computer"
        )
        self._lock = threading.Lock()
        if self._enabled:
            self._prune(max(1, retention_days))

    @property
    def root(self) -> Path:
        return self._root

    @property
    def enabled(self) -> bool:
        return self._enabled

    def path(self, now: datetime | None = None) -> Path:
        day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        return self._root / f"audit-{day}.jsonl"

    def write(self, event: str, **fields: Any) -> None:
        if not self._enabled:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "event": event,
            **fields,
        }
        try:
            line = json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            with self._lock:
                self._root.mkdir(parents=True, exist_ok=True)
                with self.path().open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except (OSError, TypeError, ValueError):
            # Auditing must never break a control session.
            return

    def _prune(self, retention_days: int) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).date()
        try:
            entries = list(self._root.iterdir())
        except OSError:
            return
        for entry in entries:
            match = _AUDIT_FILE.match(entry.name)
            if match is None:
                continue
            try:
                day = datetime.strptime(
                    match.group(1),
                    "%Y-%m-%d",
                ).date()
            except ValueError:
                continue
            if day < cutoff:
                try:
                    entry.unlink()
                except OSError:
                    pass


__all__ = ["AuditLogger"]

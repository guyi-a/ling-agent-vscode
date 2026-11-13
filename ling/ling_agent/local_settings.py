"""Project-local approval preferences stored in ``.agents``."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SETTINGS_RELATIVE_PATH = ".agents/settings.local.json"
_DISALLOWED_SEGMENT = re.compile(r"[$`<>\r\n]")
_COMMAND_TOKEN = re.compile(
    r"""^\s*(?:"([^"]+)"|'([^']+)'|(\S+))"""
)
_MAX_COMPOUND_RULES = 5


class LocalSettingsError(RuntimeError):
    pass


class LocalSettingsStore:
    """Read and update per-workspace command allow rules."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.path = (
            Path(workspace_root).expanduser().resolve()
            / SETTINGS_RELATIVE_PATH
        )

    def allows_command(self, command: str) -> bool:
        segments = split_compound_command(command)
        if segments is None:
            return False
        if any(_DISALLOWED_SEGMENT.search(segment) for segment in segments):
            return False
        patterns = self._allow_commands()
        return all(
            any(_matches_command(segment, pattern) for pattern in patterns)
            for segment in segments
        )

    def allow_commands(self, patterns: list[str]) -> None:
        normalized: list[str] = []
        for pattern in patterns:
            value = pattern.strip()
            if (
                not value
                or len(value) > 256
                or "\n" in value
                or "\r" in value
            ):
                raise ValueError("Invalid command allow pattern")
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            return
        document = self._read_document()
        permissions = document.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
            document["permissions"] = permissions
        commands = permissions.get("allowCommands")
        if not isinstance(commands, list):
            commands = []
        for value in normalized:
            if value not in commands:
                commands.append(value)
        permissions["allowCommands"] = commands
        self._write_document(document)

    def allow_command(self, pattern: str) -> None:
        self.allow_commands([pattern])

    def _allow_commands(self) -> list[str]:
        document = self._read_document()
        permissions = document.get("permissions")
        if not isinstance(permissions, dict):
            return []
        commands = permissions.get("allowCommands")
        if not isinstance(commands, list):
            return []
        return [
            value.strip()
            for value in commands
            if isinstance(value, str) and value.strip()
        ]

    def _read_document(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_document(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.tmp-{os.getpid()}"
        )
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def command_allow_patterns(command: str) -> list[str] | None:
    """Build one ``executable:*`` rule per subcommand.

    Compound commands are split on shell separators. If any part cannot be
    represented safely, or there are more than five subcommands, no rules
    are offered.
    """
    segments = split_compound_command(command)
    if segments is None or len(segments) > _MAX_COMPOUND_RULES:
        return None
    patterns: list[str] = []
    for segment in segments:
        pattern = _segment_pattern(segment)
        if pattern is None:
            return None
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns or None


def command_allow_pattern(command: str) -> str | None:
    """Return one rule for a single command, or ``None`` for compounds."""
    patterns = command_allow_patterns(command)
    if patterns is None or len(patterns) != 1:
        return None
    return patterns[0]


def split_compound_command(command: str) -> list[str] | None:
    """Split a command on shell separators while respecting quotes."""
    text = command.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return None
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            current.append(char)
            if (
                quote == '"'
                and char == "`"
                and index + 1 < len(text)
            ):
                current.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        pair = text[index : index + 2]
        if pair in ("&&", "||", "|&"):
            segments.append("".join(current).strip())
            current = []
            index += 2
            continue
        if char in (";", "|", "&", "\n"):
            segments.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quote is not None:
        return None
    segments.append("".join(current).strip())
    if not segments or any(not segment for segment in segments):
        return None
    return segments


def _segment_pattern(segment: str) -> str | None:
    if not segment or _DISALLOWED_SEGMENT.search(segment):
        return None
    match = _COMMAND_TOKEN.match(segment)
    if match is None:
        return None
    token = next(
        (item for item in match.groups() if item),
        "",
    ).strip()
    if not token or "=" in token:
        return None
    return f"{token}:*"


def _matches_command(command: str, pattern: str) -> bool:
    if pattern.endswith(":*"):
        prefix = pattern[:-2].strip()
        if not prefix:
            return False
        return (
            command == prefix
            or command.startswith(f"{prefix} ")
            or command.startswith(f"{prefix}\t")
        )
    return command == pattern


__all__ = [
    "LocalSettingsError",
    "LocalSettingsStore",
    "SETTINGS_RELATIVE_PATH",
    "command_allow_pattern",
    "command_allow_patterns",
    "split_compound_command",
]

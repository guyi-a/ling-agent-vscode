"""Tests for project-local command allow rules."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ling_agent.local_settings import (
    LocalSettingsStore,
    SETTINGS_RELATIVE_PATH,
    command_allow_pattern,
    command_allow_patterns,
    split_compound_command,
)


def test_command_allow_pattern_uses_first_token() -> None:
    assert command_allow_pattern("npm install") == "npm:*"
    assert command_allow_pattern("git status") == "git:*"
    assert (
        command_allow_pattern(
            '"C:\\Program Files\\nodejs\\npm.cmd" install'
        )
        == "C:\\Program Files\\nodejs\\npm.cmd:*"
    )
    assert command_allow_pattern("npm install && rm -rf build") is None
    assert command_allow_pattern("npm install | tee log") is None
    assert command_allow_pattern("FOO=1 npm install") is None


def test_compound_commands_split_and_build_rules() -> None:
    assert split_compound_command("git status && npm test") == [
        "git status",
        "npm test",
    ]
    assert split_compound_command(
        'echo "a && b" && npm test'
    ) == ['echo "a && b"', "npm test"]
    assert command_allow_patterns(
        "git status && npm test"
    ) == ["git:*", "npm:*"]
    assert command_allow_patterns(
        'echo "a && b" && npm test'
    ) == ["echo:*", "npm:*"]
    assert command_allow_patterns("npm install &&") is None
    assert command_allow_patterns("npm install > log.txt") is None
    assert command_allow_patterns("echo $(whoami)") is None
    assert command_allow_patterns(
        "a && b && c && d && e && f"
    ) is None


def test_quoted_command_token_matches_stored_rule() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = LocalSettingsStore(root)
        command = (
            'Start-Sleep -Seconds 20; "slept 20s, now " '
            '+ (Get-Date -Format "HH:mm:ss")'
        )
        patterns = command_allow_patterns(command)
        assert patterns == ["Start-Sleep:*", "slept 20s, now:*"]

        store.allow_commands(patterns)
        assert store.allows_command(command)

        quoted = '"C:\\Program Files\\nodejs\\npm.cmd" install'
        store.allow_commands(command_allow_patterns(quoted) or [])
        assert store.allows_command(quoted)


def test_allow_rules_match_exact_and_prefix() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = LocalSettingsStore(root)
        assert not store.allows_command("npm install")

        store.allow_command("git status")
        store.allow_command("npm:*")

        assert store.allows_command("git status")
        assert not store.allows_command("git status --short")
        assert store.allows_command("npm")
        assert store.allows_command("npm install")
        assert store.allows_command("npm    install")
        assert not store.allows_command("pnpm install")
        assert not store.allows_command("npm-not-really")
        assert not store.allows_command("npm install && echo done")

        document = json.loads(
            (root / SETTINGS_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        assert document["permissions"]["allowCommands"] == [
            "git status",
            "npm:*",
        ]


def test_compound_requires_every_subcommand_allowed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = LocalSettingsStore(root)
        store.allow_commands(["git:*", "npm:*"])

        assert store.allows_command("git status && npm test")
        assert not store.allows_command("git status && pnpm test")
        assert not store.allows_command("git status && npm test > log")


def test_allow_rule_write_preserves_other_settings() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / SETTINGS_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "permissions": {"allowCommands": ["git status"]},
                    "theme": "dark",
                }
            ),
            encoding="utf-8",
        )

        LocalSettingsStore(root).allow_command("npm:*")

        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["theme"] == "dark"
        assert document["permissions"]["allowCommands"] == [
            "git status",
            "npm:*",
        ]


def test_malformed_settings_file_is_tolerated() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / SETTINGS_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")

        store = LocalSettingsStore(root)
        assert not store.allows_command("npm install")

        store.allow_command("npm:*")
        assert store.allows_command("npm install")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

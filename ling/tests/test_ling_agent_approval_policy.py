"""Tests for the three-mode approval policy matrix."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ling_ai.types import ToolCall
from ling_agent import (
    ApprovalMode,
    FilesystemReadEffect,
    FilesystemWriteEffect,
    MetaEffect,
    NetworkEffect,
    ProcessExecEffect,
    SubagentEffect,
    UnknownEffect,
    Workspace,
    approval_reason,
    derive_effect,
)


def _write_call(path: str, content: str = "hello") -> ToolCall:
    return ToolCall(
        id="call-1",
        name="write_file",
        arguments={"path": path, "content": content},
    )


def test_search_codebase_is_read_only() -> None:
    with tempfile.TemporaryDirectory() as temp:
        effect = derive_effect(
            ToolCall(
                id="call-1",
                name="search_codebase",
                arguments={"query": "load skill"},
            ),
            Workspace(Path(temp)),
        )
    assert isinstance(effect, FilesystemReadEffect)
    assert approval_reason(effect, ApprovalMode.MANUAL) is None


def test_approval_policy_matrix() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        read = FilesystemReadEffect(operation="read", path=root / "a.txt")
        write = FilesystemWriteEffect(operation="write", path=root / "a.txt")
        harmless = ProcessExecEffect(
            command="Get-Location",
            cwd=root,
            classification="harmless",
        )
        normal = ProcessExecEffect(
            command="npm test",
            cwd=root,
            classification="normal",
        )
        destructive = ProcessExecEffect(
            command="git reset --hard",
            cwd=root,
            classification="destructive",
        )
        private_network = NetworkEffect(
            operation="fetch",
            target="http://127.0.0.1:8080",
            private_host=True,
        )
        public_network = NetworkEffect(
            operation="fetch",
            target="https://example.com",
            private_host=False,
        )
        unknown = UnknownEffect(note="unknown tool")
        meta = MetaEffect(operation="todo")
        subagent = SubagentEffect()

        modes = [
            ApprovalMode.MANUAL,
            ApprovalMode.ACCEPT_WRITE,
            ApprovalMode.AUTO,
        ]

        assert [approval_reason(read, mode) for mode in modes] == [None, None, None]
        assert [approval_reason(meta, mode) for mode in modes] == [None, None, None]
        assert [approval_reason(subagent, mode) for mode in modes] == [
            None,
            None,
            None,
        ]
        assert [approval_reason(write, mode) for mode in modes] == [
            f"write {root / 'a.txt'}",
            None,
            None,
        ]
        assert [approval_reason(harmless, mode) for mode in modes] == [
            None,
            None,
            None,
        ]
        assert [approval_reason(normal, mode) for mode in modes] == [
            "normal command",
            "normal command",
            None,
        ]
        assert [approval_reason(destructive, mode) for mode in modes] == [
            "destructive command",
            "destructive command",
            "destructive command",
        ]
        assert [approval_reason(private_network, mode) for mode in modes] == [
            "private network URL is blocked",
            "private network URL is blocked",
            "private network URL is blocked",
        ]
        assert [approval_reason(public_network, mode) for mode in modes] == [
            None,
            None,
            None,
        ]
        assert [approval_reason(unknown, mode) for mode in modes] == [
            "unknown tool",
            "unknown tool",
            "unknown tool",
        ]


def test_sensitive_write_is_never_auto_allowed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        sensitive_path = FilesystemWriteEffect(
            operation="write",
            path=root / ".env",
        )
        sensitive_content = FilesystemWriteEffect(
            operation="write",
            path=root / "config.txt",
        )
        call = _write_call("config.txt", "API_KEY=secret")

        for mode in ApprovalMode:
            assert approval_reason(sensitive_path, mode) == "sensitive file"
            assert (
                approval_reason(sensitive_content, mode, call)
                == "content may contain credentials"
            )


if __name__ == "__main__":
    test_approval_policy_matrix()
    test_sensitive_write_is_never_auto_allowed()
    print("all ling_agent approval policy tests passed")

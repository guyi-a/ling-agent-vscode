"""Tests for the three-mode approval policy matrix."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ling_ai.types import ToolCall
from ling_agent import (
    ApprovalMode,
    BrowserEffect,
    ComputerEffect,
    FilesystemReadEffect,
    FilesystemWriteEffect,
    McpAdminEffect,
    McpEffect,
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
            None,
        ]
        assert [approval_reason(private_network, mode) for mode in modes] == [
            "private network URL is blocked",
            "private network URL is blocked",
            None,
        ]
        assert [approval_reason(public_network, mode) for mode in modes] == [
            None,
            None,
            None,
        ]
        assert [approval_reason(unknown, mode) for mode in modes] == [
            "unknown tool",
            "unknown tool",
            None,
        ]


def test_sensitive_write_requires_approval_except_auto() -> None:
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

        for mode in (
            ApprovalMode.MANUAL,
            ApprovalMode.ACCEPT_WRITE,
            ApprovalMode.PLAN,
        ):
            assert approval_reason(sensitive_path, mode) == "sensitive file"
            assert (
                approval_reason(sensitive_content, mode, call)
                == "content may contain credentials"
            )
        assert approval_reason(
            sensitive_path,
            ApprovalMode.AUTO,
        ) is None
        assert approval_reason(
            sensitive_content,
            ApprovalMode.AUTO,
            call,
        ) is None


def test_auto_only_approves_mutating_computer_control() -> None:
    root = Path("E:/workspace")
    allowed = [
        ProcessExecEffect(
            command="git reset --hard",
            cwd=root,
            classification="destructive",
        ),
        FilesystemWriteEffect(
            operation="write",
            path=root / ".env",
        ),
        NetworkEffect(
            operation="fetch",
            target="http://127.0.0.1:8080",
            private_host=True,
        ),
        BrowserEffect(
            action="execute_script",
            classification="script",
        ),
        McpAdminEffect(operation="add_server", server="local"),
        McpEffect(
            server="remote",
            tool="delete",
            transport="http",
            target="https://example.com/mcp",
            private_host=False,
            read_only=False,
            destructive=True,
            open_world=True,
            trusted_annotations=False,
        ),
        UnknownEffect(note="unknown tool"),
    ]
    assert all(
        approval_reason(effect, ApprovalMode.AUTO) is None
        for effect in allowed
    )

    computer = ComputerEffect(
        operation="act",
        classification="mutating",
        window_id="w-1",
    )
    assert approval_reason(computer, ApprovalMode.AUTO) is not None
    readonly_computer = ComputerEffect(
        operation="observe",
        classification="readonly",
        window_id="w-1",
    )
    assert approval_reason(readonly_computer, ApprovalMode.AUTO) is None


if __name__ == "__main__":
    test_approval_policy_matrix()
    test_sensitive_write_requires_approval_except_auto()
    test_auto_only_approves_mutating_computer_control()
    print("all ling_agent approval policy tests passed")

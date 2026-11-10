"""Tests for tool effects and PowerShell command classification."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ling_ai.types import ToolCall
from ling_agent import (
    FilesystemReadEffect,
    FilesystemWriteEffect,
    ProcessExecEffect,
    UnknownEffect,
    Workspace,
    WorkspacePathError,
    classify_command,
    derive_effect,
)


def _call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def test_derive_effect_for_current_tools() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "README.md").write_text("hello", encoding="utf-8")
        workspace = Workspace(root)

        read = derive_effect(
            _call("read_file", {"path": "README.md"}),
            workspace,
        )
        assert isinstance(read, FilesystemReadEffect)
        assert read.operation == "read"
        assert read.path == (root / "README.md").resolve()

        listing = derive_effect(_call("list_files", {}), workspace)
        assert isinstance(listing, FilesystemReadEffect)
        assert listing.operation == "list"
        assert listing.path == root.resolve()

        glob_effect = derive_effect(
            _call("glob", {"pattern": "**/*.md"}),
            workspace,
        )
        assert isinstance(glob_effect, FilesystemReadEffect)
        assert glob_effect.operation == "glob"
        assert glob_effect.path == root.resolve()

        write = derive_effect(
            _call("write_file", {"path": "a.txt", "content": "x"}),
            workspace,
        )
        assert isinstance(write, FilesystemWriteEffect)
        assert write.operation == "write"

        edit = derive_effect(
            _call(
                "edit_file",
                {"path": "README.md", "old_text": "a", "new_text": "b"},
            ),
            workspace,
        )
        assert isinstance(edit, FilesystemWriteEffect)
        assert edit.operation == "edit"

        command = derive_effect(
            _call("run_command", {"command": "Get-Content README.md"}),
            workspace,
        )
        assert isinstance(command, ProcessExecEffect)
        assert command.classification == "harmless"
        assert command.cwd == root.resolve()

        unknown = derive_effect(_call("unknown_tool", {}), workspace)
        assert isinstance(unknown, UnknownEffect)


def test_derive_effect_rejects_path_escape() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        try:
            derive_effect(
                _call("read_file", {"path": "../outside.txt"}),
                workspace,
            )
        except WorkspacePathError:
            pass
        else:
            raise AssertionError("Expected workspace path rejection")


def test_powershell_command_classification() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "README.md").write_text("hello", encoding="utf-8")
        workspace = Workspace(root)

        harmless = [
            "Get-Location",
            "pwd",
            "Get-ChildItem",
            "Get-ChildItem .",
            "Get-ChildItem -Force",
            "Get-Content README.md",
            "Get-Content -LiteralPath README.md",
            "git status",
            "git diff",
            "git log --oneline",
            "git show HEAD",
        ]
        for command in harmless:
            assert classify_command(command, workspace) == "harmless", command

        normal = [
            "npm test",
            "Get-Content ..\\outside.txt",
            "Get-Content C:\\Windows\\win.ini",
            "Get-Content $env:SECRET",
            "Get-Content README.md | Select-Object -First 1",
            "git diff --output=diff.txt",
            "Set-Content README.md -Value changed",
        ]
        for command in normal:
            assert classify_command(command, workspace) == "normal", command

        destructive = [
            "Remove-Item README.md -Force",
            "Remove-Item build -Recurse",
            "rd /s build",
            "git reset --hard",
            "git clean -fd",
            "Stop-Computer",
            "diskpart",
        ]
        for command in destructive:
            assert classify_command(command, workspace) == "destructive", command

        assert classify_command("git status; Remove-Item x", workspace) == "destructive"


if __name__ == "__main__":
    test_derive_effect_for_current_tools()
    test_derive_effect_rejects_path_escape()
    test_powershell_command_classification()
    print("all ling_agent effect tests passed")

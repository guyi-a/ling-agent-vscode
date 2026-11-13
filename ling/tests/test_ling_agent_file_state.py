"""Tests for read-before-edit file state tracking."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from ling_agent import FileReadTracker, Workspace
from ling_agent.file_state import create_read_guard_hook
from ling_agent.tools.files import create_file_tools, create_mutation_tools
from ling_ai.types import ToolCall
from ling_core import PreToolUse, ToolContext, ToolExecution

CONTEXT = ToolContext(step=1)


def _tool(tools, name: str):
    return next(tool for tool in tools if tool.definition.name == name)


def _call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def _setup(root: Path):
    workspace = Workspace(root)
    tracker = FileReadTracker()
    reads = create_file_tools(workspace, file_reads=tracker)
    mutations = create_mutation_tools(workspace, file_reads=tracker)
    return workspace, tracker, reads, mutations


def test_edit_requires_prior_read() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target = root / "a.txt"
        target.write_text("hello", encoding="utf-8")
        _, _, _, mutations = _setup(root)

        result = _tool(mutations, "edit_file").handler(
            _call(
                "edit_file",
                path="a.txt",
                old_text="hello",
                new_text="goodbye",
            ),
            CONTEXT,
        )

        assert isinstance(result, ToolExecution)
        assert result.is_error is True
        assert "has not been read" in result.output
        assert target.read_text(encoding="utf-8") == "hello"


def test_read_then_repeated_edits_do_not_require_rereading() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target = root / "a.txt"
        target.write_text("one", encoding="utf-8")
        _, _, reads, mutations = _setup(root)

        read_result = _tool(reads, "read_file").handler(
            _call("read_file", path="a.txt", line_limit=1),
            CONTEXT,
        )
        assert "one" in read_result

        edit = _tool(mutations, "edit_file")
        first = edit.handler(
            _call(
                "edit_file",
                path="a.txt",
                old_text="one",
                new_text="two",
            ),
            CONTEXT,
        )
        second = edit.handler(
            _call(
                "edit_file",
                path="a.txt",
                old_text="two",
                new_text="three",
            ),
            CONTEXT,
        )

        assert not isinstance(first, ToolExecution) or not first.is_error
        assert not isinstance(second, ToolExecution) or not second.is_error
        assert target.read_text(encoding="utf-8") == "three"


def test_write_new_file_does_not_require_read() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _, _, _, mutations = _setup(root)

        result = _tool(mutations, "write_file").handler(
            _call("write_file", path="new.txt", content="hello"),
            CONTEXT,
        )

        assert not isinstance(result, ToolExecution) or not result.is_error
        assert (root / "new.txt").read_text(encoding="utf-8") == "hello"


def test_overwriting_existing_file_requires_read() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target = root / "a.txt"
        target.write_text("old", encoding="utf-8")
        _, _, reads, mutations = _setup(root)
        write = _tool(mutations, "write_file")
        call = _call("write_file", path="a.txt", content="new")

        blocked = write.handler(call, CONTEXT)
        assert isinstance(blocked, ToolExecution)
        assert blocked.is_error is True
        assert "has not been read" in blocked.output

        _tool(reads, "read_file").handler(
            _call("read_file", path="a.txt"),
            CONTEXT,
        )
        allowed = write.handler(call, CONTEXT)
        assert not isinstance(allowed, ToolExecution) or not allowed.is_error
        assert target.read_text(encoding="utf-8") == "new"


def test_external_change_requires_rereading() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target = root / "a.txt"
        target.write_text("one", encoding="utf-8")
        _, _, reads, mutations = _setup(root)
        read = _tool(reads, "read_file")
        edit = _tool(mutations, "edit_file")
        call = _call(
            "edit_file",
            path="a.txt",
            old_text="external",
            new_text="edited",
        )

        read.handler(_call("read_file", path="a.txt"), CONTEXT)
        stamp = target.stat()
        target.write_text("external", encoding="utf-8")
        os.utime(
            target,
            ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000),
        )

        blocked = edit.handler(call, CONTEXT)
        assert isinstance(blocked, ToolExecution)
        assert blocked.is_error is True
        assert "changed on disk" in blocked.output

        read.handler(_call("read_file", path="a.txt"), CONTEXT)
        allowed = edit.handler(call, CONTEXT)
        assert not isinstance(allowed, ToolExecution) or not allowed.is_error
        assert target.read_text(encoding="utf-8") == "edited"


def test_guard_hook_blocks_before_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "a.txt").write_text("hello", encoding="utf-8")
        workspace = Workspace(root)
        tracker = FileReadTracker()
        reads = create_file_tools(workspace, file_reads=tracker)
        hook = create_read_guard_hook(workspace, tracker)
        edit_call = _call(
            "edit_file",
            path="a.txt",
            old_text="hello",
            new_text="bye",
        )

        denied = hook(PreToolUse(call=edit_call, context=CONTEXT))
        assert denied is not None
        assert denied.allowed is False
        assert "has not been read" in (denied.reason or "")

        _tool(reads, "read_file").handler(
            _call("read_file", path="a.txt"),
            CONTEXT,
        )
        assert hook(PreToolUse(call=edit_call, context=CONTEXT)) is None

        new_file = _call("write_file", path="new.txt", content="x")
        assert hook(PreToolUse(call=new_file, context=CONTEXT)) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

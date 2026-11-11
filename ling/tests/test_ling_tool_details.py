"""Tests for structured tool card details."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ling_agent import ToolTimelineItem, Workspace
from ling_agent.tools.files import create_mutation_tools
from ling_ai.types import ToolCall
from ling_core import ToolContext, ToolExecution
from ling_rpc.ui_events import _tool_entry


def _tool(tools: list, name: str):
    return next(
        item for item in tools if item.definition.name == name
    )


def _item(
    name: str,
    arguments: dict,
    *,
    output: str = "",
    details: object | None = None,
) -> ToolTimelineItem:
    return ToolTimelineItem(
        id="call-1",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=1,
        name=name,
        arguments=arguments,
        status="completed",
        output=output,
        details=details,
    )


def test_edit_tool_entry_has_diff_detail() -> None:
    item = _item(
        "edit_file",
        {
            "path": "a.py",
            "old_text": "def f():\n    return 1\n",
            "new_text": "def f():\n    return 2\n",
        },
        output="Edited a.py",
        details={"kind": "edit", "startLine": 3},
    )

    detail = _tool_entry(
        item,
        waiting_for_approval=False,
    )["tool"]["detail"]

    assert detail["kind"] == "diff"
    assert detail["path"] == "a.py"
    assert detail["added"] == 1
    assert detail["removed"] == 1
    assert any(
        row["type"] == "remove"
        and row["oldLine"] == 4
        and "return 1" in row["text"]
        for row in detail["rows"]
    )
    assert any(
        row["type"] == "add"
        and row["newLine"] == 4
        and "return 2" in row["text"]
        for row in detail["rows"]
    )


def test_write_tool_entry_has_previous_content_diff() -> None:
    item = _item(
        "write_file",
        {"path": "a.py", "content": "line1\nnew\n"},
        output="Wrote 11 bytes to a.py",
        details={
            "kind": "write",
            "previousContent": "line1\nold\n",
            "previousExists": True,
            "previousTruncated": False,
        },
    )

    detail = _tool_entry(
        item,
        waiting_for_approval=False,
    )["tool"]["detail"]

    assert detail["kind"] == "diff"
    assert detail["added"] == 1
    assert detail["removed"] == 1
    assert detail["truncated"] is False


def test_run_tool_entry_has_io_detail() -> None:
    item = _item(
        "run_command",
        {"command": "python a.py"},
        output="exit_code: 0\nstdout:\n1\n2\n",
    )

    detail = _tool_entry(
        item,
        waiting_for_approval=False,
    )["tool"]["detail"]

    assert detail == {
        "kind": "io",
        "input": "python a.py",
        "output": "exit_code: 0\nstdout:\n1\n2\n",
    }


def test_mutation_tools_attach_ui_details() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "a.py").write_text(
            "line1\nold\n",
            encoding="utf-8",
        )
        tools = create_mutation_tools(Workspace(root))

        write = _tool(tools, "write_file")
        result = write.handler(
            ToolCall(
                id="call-1",
                name="write_file",
                arguments={
                    "path": "a.py",
                    "content": "line1\nnew\n",
                },
            ),
            ToolContext(step=1),
        )
        assert isinstance(result, ToolExecution)
        assert result.details["previousContent"] == "line1\nold\n"
        assert result.details["previousExists"] is True

        edit = _tool(tools, "edit_file")
        result = edit.handler(
            ToolCall(
                id="call-2",
                name="edit_file",
                arguments={
                    "path": "a.py",
                    "old_text": "new",
                    "new_text": "newer",
                },
            ),
            ToolContext(step=1),
        )
        assert isinstance(result, ToolExecution)
        assert result.details["startLine"] == 2

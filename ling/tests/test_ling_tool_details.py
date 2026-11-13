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


def test_web_tool_entries_use_web_action() -> None:
    search = _item(
        "web_search",
        {"query": "DeepSeek docs"},
        output='{"ok":true,"results":[]}',
    )
    fetch = _item(
        "web_fetch",
        {"url": "https://api-docs.deepseek.com/"},
        output='{"ok":true,"title":"Docs"}',
    )

    search_entry = _tool_entry(
        search,
        waiting_for_approval=False,
    )
    fetch_entry = _tool_entry(
        fetch,
        waiting_for_approval=False,
    )

    assert search_entry["tool"]["action"] == "Web"
    assert search_entry["tool"]["target"] == "DeepSeek docs"
    assert search_entry["tool"]["detail"]["input"] == "DeepSeek docs"
    assert fetch_entry["tool"]["action"] == "Web"
    assert fetch_entry["tool"]["target"].startswith(
        "https://api-docs.deepseek.com/"
    )
    assert (
        fetch_entry["tool"]["detail"]["input"]
        == "https://api-docs.deepseek.com/"
    )


def test_memory_tool_entries_use_memory_action() -> None:
    item = _item(
        "memory_write",
        {
            "name": "testing",
            "scope": "workspace",
        },
        output=(
            '{"ok":true,"scope":"workspace","name":"testing",'
            '"path":"E:/workspace/.agents/memory/testing.md"}'
        ),
    )
    entry = _tool_entry(item, waiting_for_approval=False)

    assert entry["tool"]["action"] == "Memory"
    assert entry["tool"]["target"] == "workspace:testing"
    assert entry["tool"]["detail"]["kind"] == "memory"
    assert entry["tool"]["detail"]["operation"] == "write"
    assert entry["tool"]["detail"]["scope"] == "workspace"
    assert entry["tool"]["detail"]["name"] == "testing"


def test_memory_list_has_structured_detail() -> None:
    item = _item(
        "memory_list",
        {},
        output=(
            '{"ok":true,"memories":[{"name":"style",'
            '"description":"Use concise output.",'
            '"scope":"workspace","alwaysApply":true,'
            '"globs":[],"path":".agents/memory/style.md"}],'
            '"diagnostics":[]}'
        ),
    )

    detail = _tool_entry(
        item,
        waiting_for_approval=False,
    )["tool"]["detail"]

    assert detail["kind"] == "memory"
    assert detail["operation"] == "list"
    assert detail["ok"] is True
    assert detail["memories"] == [
        {
            "name": "style",
            "description": "Use concise output.",
            "scope": "workspace",
            "alwaysApply": True,
            "globs": [],
            "path": ".agents/memory/style.md",
        }
    ]


def test_load_skill_has_markdown_detail() -> None:
    item = _item(
        "load_skill",
        {"name": "inline-visualization"},
        output=(
            '{"ok":true,"name":"inline-visualization",'
            '"description":"Render compact diagrams.",'
            '"body":"# Diagram\\n\\nUse an SVG.",'
            '"skill_path":"E:/skills/inline-visualization",'
            '"scripts_path":"E:/skills/inline-visualization/scripts"}'
        ),
    )

    detail = _tool_entry(
        item,
        waiting_for_approval=False,
    )["tool"]["detail"]

    assert detail == {
        "kind": "skill",
        "name": "inline-visualization",
        "description": "Render compact diagrams.",
        "body": "# Diagram\n\nUse an SVG.",
        "skillPath": "E:/skills/inline-visualization",
        "scriptsPath": "E:/skills/inline-visualization/scripts",
        "ok": True,
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

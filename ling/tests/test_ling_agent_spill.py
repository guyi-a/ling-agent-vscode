"""Tests for spilling oversized tool output."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.types import ToolCall
from ling_core import HookRegistry, PostToolUse, ToolContext

from ling_agent import HarnessConfig, Workspace
from ling_agent.tools.formatting import join_limited
from ling_agent.tools.spill import create_spill_hook, maybe_spill_output


def _config(
    *,
    spill_threshold_chars: int = 50,
    spill_preview_chars: int = 20,
    max_tool_result_chars: int = 500,
) -> HarnessConfig:
    return HarnessConfig(
        spill_threshold_chars=spill_threshold_chars,
        spill_preview_chars=spill_preview_chars,
        max_tool_result_chars=max_tool_result_chars,
    )


def test_spill_writes_large_output() -> None:
    with tempfile.TemporaryDirectory() as temp:
        config = _config()
        workspace = Workspace(temp, config)
        text = "x" * 80

        result = maybe_spill_output(
            workspace,
            session_id="session-1",
            call_id="call-1",
            text=text,
            config=config,
        )

        assert result.spilled
        assert result.path == ".agents/spills/session-1/call-1.txt"
        assert "Output spilled to" in result.text
        assert "read_file" in result.text
        spilled = Path(temp) / result.path
        assert spilled.read_text(encoding="utf-8") == text


def test_small_output_is_not_spilled() -> None:
    with tempfile.TemporaryDirectory() as temp:
        config = _config()
        workspace = Workspace(temp, config)
        result = maybe_spill_output(
            workspace,
            session_id="session-1",
            call_id="call-1",
            text="small",
            config=config,
        )

        assert not result.spilled
        assert result.text == "small"
        assert not (Path(temp) / ".agents").exists()


def test_spill_truncates_when_write_fails() -> None:
    with tempfile.TemporaryDirectory() as temp:
        config = _config(max_tool_result_chars=60)
        workspace = Workspace(temp, config)
        (Path(temp) / ".agents").write_text("blocked", encoding="utf-8")

        result = maybe_spill_output(
            workspace,
            session_id="session-1",
            call_id="call-1",
            text="y" * 80,
            config=config,
        )

        assert not result.spilled
        assert result.path is None
        assert result.text.endswith("[truncated]")
        assert len(result.text) <= 60 + len("\n\n[truncated]")


def test_post_tool_hook_sees_spilled_output() -> None:
    with tempfile.TemporaryDirectory() as temp:
        config = _config()
        workspace = Workspace(temp, config)
        registry = HookRegistry()
        registry.register(
            PostToolUse,
            create_spill_hook(
                workspace,
                session_id="session-1",
                config=config,
            ),
        )
        seen: list[object] = []
        registry.register(
            PostToolUse,
            lambda event: seen.append(event.output),
        )

        event = PostToolUse(
            call=ToolCall(id="call-1", name="read_file", arguments={}),
            output="z" * 80,
            context=ToolContext(step=1),
        )
        decisions = asyncio.run(registry.dispatch(event))

        assert decisions
        assert len(seen) == 1
        assert isinstance(seen[0], str)
        assert "Output spilled to" in seen[0]
        assert "Output spilled to" in event.output


def test_join_limited_adds_truncation_marker() -> None:
    assert join_limited([], 10, "(empty)") == "(empty)"
    assert join_limited(["a", "b"], 10, "(empty)") == "a\nb"
    assert join_limited(["x" * 20], 10, "(empty)") == "[truncated]"


def test_grep_caps_total_output() -> None:
    with tempfile.TemporaryDirectory() as temp:
        config = _config(max_tool_result_chars=100)
        workspace = Workspace(temp, config)
        (Path(temp) / "a.txt").write_text(
            "needle " + ("x" * 500),
            encoding="utf-8",
        )

        matches = workspace.grep("needle")
        output = "\n".join(matches)

        assert "needle" in output
        assert "[truncated]" in output
        assert len(output) <= 120

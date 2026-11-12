"""Tests for ling_agent system prompt assembly."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ling_agent import HarnessConfig, Skill, Workspace, build_system_prompt


def test_prompt_includes_workspace_and_agents_file() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "AGENTS.md").write_text(
            "Always run tests after edits.",
            encoding="utf-8",
        )
        workspace = Workspace(root)

        prompt = build_system_prompt(workspace)

        assert str(workspace.root) in prompt.system_prompt
        assert "Always run tests after edits." in prompt.system_prompt
        assert "read_file" in prompt.system_prompt
        assert "load_skill" in prompt.system_prompt
        assert "extract_document_text" in prompt.system_prompt
        assert "## Core rules" in prompt.system_prompt
        assert "## Tools" in prompt.system_prompt
        assert "## User-provided context" in prompt.system_prompt
        assert "## Asking the user" in prompt.system_prompt
        assert "## Completion" in prompt.system_prompt
        assert "## Safety" in prompt.system_prompt
        assert "AGENTS.md" in prompt.sources
        assert not prompt.truncated


def test_prompt_has_size_limit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "AGENTS.md").write_text("x" * 500, encoding="utf-8")
        workspace = Workspace(root)

        prompt = build_system_prompt(
            workspace,
            config=HarnessConfig(prompt_max_chars=120),
        )

        assert prompt.truncated
        assert prompt.system_prompt.endswith("[prompt truncated]")


def test_prompt_emphasizes_inline_visualization() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        skill = Skill(
            name="inline-visualization",
            description="Render diagrams.",
            body="Use an SVG.",
            dir_path=Path(temp),
        )
        prompt = build_system_prompt(
            workspace,
            skills=(skill,),
        )

    assert "## Inline visualization" in prompt.system_prompt
    assert 'load_skill(name="inline-visualization")' in prompt.system_prompt
    assert 'type="renderer"' in prompt.system_prompt
    assert "plain `html` or `svg` fence" in prompt.system_prompt
    assert "inline-visualization" in prompt.sources


if __name__ == "__main__":
    test_prompt_includes_workspace_and_agents_file()
    test_prompt_has_size_limit()
    test_prompt_emphasizes_inline_visualization()
    print("all ling_agent prompt tests passed")

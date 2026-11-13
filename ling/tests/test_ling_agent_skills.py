"""Tests for skill discovery, prompt injection, and load_skill."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ling_agent import (
    HarnessConfig,
    Workspace,
    build_system_prompt,
    create_file_tools,
    create_skill_tools,
    format_skills_index,
    load_skills,
    workspace_skills_root,
)
from ling_agent.tools.spill import create_spill_hook
from ling_agent.workspace import WorkspacePathError
from ling_ai.types import ToolCall
from ling_core import PostToolUse, ToolContext

CONTEXT = ToolContext(step=1)


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "A test skill",
    body: str = "Skill body.",
    extra: str = "",
    raw: str | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = (
        raw
        if raw is not None
        else (
            "---\n"
            f"name: {name}\n"
            f'description: "{description}"\n'
            f"{extra}"
            "---\n\n"
            f"{body}\n"
        )
    )
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def _call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def test_load_skills_parses_frontmatter_and_body() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "skills"
        _write_skill(
            root,
            "pdf",
            description="PDF toolbox",
            body="# PDF\n\nUse pypdf.",
            extra="disable-model-invocation: false\n",
        )

        result = load_skills(root, Path(temp) / "workspace")
        assert len(result.skills) == 1
        skill = result.skills[0]
        assert skill.name == "pdf"
        assert skill.description == "PDF toolbox"
        assert skill.body == "# PDF\n\nUse pypdf."
        assert skill.dir_path == (root / "pdf").resolve()
        assert not skill.disable_model_invocation


def test_builtin_skill_wins_over_global_and_workspace() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        global_root = Path(temp) / "global"
        workspace_root = Path(temp) / "workspace"
        _write_skill(builtin, "pdf", description="bundled")
        _write_skill(global_root, "pdf", description="global")
        _write_skill(
            workspace_skills_root(workspace_root),
            "pdf",
            description="project",
        )

        result = load_skills(
            builtin,
            workspace_root,
            global_root=global_root,
        )
        assert [skill.description for skill in result.skills] == ["bundled"]
        assert not result.diagnostics


def test_global_untagged_skill_loads_and_managed_copy_is_skipped() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        global_root = Path(temp) / "global"
        workspace_root = Path(temp) / "workspace"
        _write_skill(builtin, "pdf", description="bundled")
        _write_skill(global_root, "review", description="user global")
        _write_skill(
            global_root,
            "mirror",
            description="managed mirror",
            extra="source: ling\n",
        )

        result = load_skills(
            builtin,
            workspace_root,
            global_root=global_root,
        )
        descriptions = {
            skill.name: skill.description for skill in result.skills
        }
        assert descriptions == {
            "pdf": "bundled",
            "review": "user global",
        }


def test_global_skill_wins_over_workspace() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace_root = Path(temp) / "workspace"
        global_root = Path(temp) / "global"
        _write_skill(global_root, "review", description="global")
        _write_skill(
            workspace_skills_root(workspace_root),
            "review",
            description="project",
        )

        result = load_skills(
            None,
            workspace_root,
            global_root=global_root,
        )
        assert [skill.description for skill in result.skills] == ["global"]


def test_frontmatter_parses_block_scalars_and_source() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "skills"
        _write_skill(
            root,
            "review",
            raw=(
                "---\n"
                "name: review\n"
                "description: >-\n"
                "  Review code changes with the\n"
                "  security subagent.\n"
                "source: ling\n"
                "metadata:\n"
                "  surface: ide\n"
                "---\n\n"
                "Body\n"
            ),
        )
        _write_skill(
            root,
            "canvas",
            raw=(
                "---\n"
                "name: canvas\n"
                "description: |\n"
                "  Line one.\n"
                "  Line two.\n"
                "---\n\n"
                "Body\n"
            ),
        )

        result = load_skills(root, Path(temp) / "workspace")
        by_name = {skill.name: skill for skill in result.skills}
        assert (
            by_name["review"].description
            == "Review code changes with the security subagent."
        )
        assert by_name["review"].source == "ling"
        assert by_name["canvas"].description == "Line one.\nLine two."
        assert by_name["canvas"].source is None


def test_invalid_workspace_skill_does_not_shadow_builtin() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        workspace_root = Path(temp) / "workspace"
        _write_skill(builtin, "pdf", description="bundled")
        _write_skill(
            workspace_skills_root(workspace_root),
            "pdf",
            description="",
        )

        result = load_skills(builtin, workspace_root)
        assert [skill.description for skill in result.skills] == ["bundled"]
        assert any("description is required" in item for item in result.diagnostics)


def test_invalid_names_and_mismatches_are_dropped() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "skills"
        _write_skill(root, "pdf", raw="---\nname: other\ndescription: x\n---\n")
        _write_skill(root, "Bad_Name", description="x")
        _write_skill(root, "ok", description="x" * 1025)

        result = load_skills(root, Path(temp) / "workspace")
        assert result.skills == ()
        joined = "\n".join(result.diagnostics)
        assert "must match directory" in joined
        assert "invalid skill name" in joined
        assert "exceeds 1024" in joined


def test_frontmatter_handles_crlf_and_single_quotes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "skills"
        _write_skill(
            root,
            "docx",
            raw=(
                "---\r\n"
                "name: docx\r\n"
                "description: 'Word tools'\r\n"
                "---\r\n\r\n"
                "Body\r\n"
            ),
        )

        result = load_skills(root, Path(temp) / "workspace")
        assert result.skills[0].description == "Word tools"
        assert result.skills[0].body == "Body"


def test_index_formats_and_filters_disabled_skills() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "skills"
        _write_skill(root, "pdf", description="PDF tools")
        _write_skill(
            root,
            "pptx",
            description="Slides",
            extra="disable-model-invocation: true\n",
        )

        result = load_skills(root, Path(temp) / "workspace")
        index = format_skills_index(result.skills)
        assert "## Skills" in index
        assert "- **pdf** — PDF tools" in index
        assert "pptx" not in index


def test_prompt_includes_skills_section() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace_root = Path(temp) / "workspace"
        workspace_root.mkdir()
        skills_root = Path(temp) / "skills"
        _write_skill(skills_root, "pdf", description="PDF tools")
        skills = load_skills(skills_root, workspace_root).skills

        prompt = build_system_prompt(
            Workspace(workspace_root),
            skills=skills,
        )
        assert "## Skills" in prompt.system_prompt
        assert "- **pdf** — PDF tools" in prompt.system_prompt
        assert "skills" in prompt.sources


def test_load_skill_tool_returns_body_and_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "skills"
        skill_dir = _write_skill(root, "pdf", body="Playbook")
        (skill_dir / "scripts").mkdir()
        tools = create_skill_tools(load_skills(root, Path(temp)).skills)

        output = tools[0].handler(
            _call("load_skill", {"name": "pdf"}),
            CONTEXT,
        )
        assert isinstance(output, str)
        payload = json.loads(output)
        assert payload["ok"] is True
        assert payload["name"] == "pdf"
        assert payload["body"] == "Playbook"
        assert payload["skill_path"] == str(skill_dir.resolve())
        assert payload["scripts_path"] == str((skill_dir / "scripts").resolve())


def test_load_skill_tool_rejects_unknown_name() -> None:
    with tempfile.TemporaryDirectory() as temp:
        tools = create_skill_tools(())
        result = tools[0].handler(
            _call("load_skill", {"name": "missing"}),
            CONTEXT,
        )
        payload = json.loads(result.output)
        assert result.is_error is True
        assert payload["ok"] is False
        assert payload["available"] == []


def test_spill_hook_exempts_load_skill() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = Workspace(Path(temp))
        hook = create_spill_hook(
            workspace,
            session_id="session-1",
            config=HarnessConfig(spill_threshold_chars=10),
            exempt_tools=frozenset({"load_skill"}),
        )

        def event(name: str) -> PostToolUse:
            return PostToolUse(
                call=_call(name, {}),
                output="x" * 100,
                context=CONTEXT,
            )

        assert hook(event("load_skill")) is None
        decision = hook(event("read_file"))
        assert decision is not None
        assert "[Output spilled" in decision.output


def test_read_file_allows_skill_root_absolute_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace_root = Path(temp) / "workspace"
        workspace_root.mkdir()
        skills_root = Path(temp) / "skills"
        skill_dir = _write_skill(skills_root, "pdf", body="Reference")
        workspace = Workspace(workspace_root)
        tools = create_file_tools(
            workspace,
            extra_read_roots=(skills_root,),
        )

        output = tools[0].handler(
            _call(
                "read_file",
                {"path": str(skill_dir / "SKILL.md")},
            ),
            CONTEXT,
        )
        assert "Reference" in output

        outside = Path(temp) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        with pytest.raises(WorkspacePathError):
            tools[0].handler(
                _call("read_file", {"path": str(outside)}),
                CONTEXT,
            )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

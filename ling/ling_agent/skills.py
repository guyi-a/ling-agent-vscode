"""Skill discovery, frontmatter parsing, and prompt formatting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SKILL_FILE = "SKILL.md"
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KNOWN_FIELDS = ("name", "description", "disable-model-invocation")


@dataclass(slots=True, frozen=True)
class Skill:
    name: str
    description: str
    body: str
    dir_path: Path
    disable_model_invocation: bool = False


@dataclass(slots=True, frozen=True)
class SkillLoadResult:
    skills: tuple[Skill, ...]
    diagnostics: tuple[str, ...]


def workspace_skills_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / ".agents" / "skills"


def load_skills(
    builtin_root: str | Path | None,
    workspace_root: str | Path,
) -> SkillLoadResult:
    """Load bundled skills first, then let workspace skills override them."""
    diagnostics: list[str] = []
    builtin = (
        _load_root(Path(builtin_root), diagnostics)
        if builtin_root is not None
        else {}
    )
    workspace = _load_root(workspace_skills_root(workspace_root), diagnostics)

    merged = dict(builtin)
    for name, skill in workspace.items():
        if name in merged:
            diagnostics.append(
                f"workspace skill '{name}' overrides the bundled skill"
            )
        merged[name] = skill

    return SkillLoadResult(
        skills=tuple(merged[name] for name in sorted(merged)),
        diagnostics=tuple(diagnostics),
    )


def format_skills_index(skills: Sequence[Skill]) -> str:
    visible = sorted(
        (
            skill
            for skill in skills
            if not skill.disable_model_invocation
        ),
        key=lambda skill: skill.name,
    )
    if not visible:
        return ""

    lines = [
        "## Skills",
        "",
        "Task-specific playbooks are available. When a request matches one "
        "of the descriptions below, call `load_skill` first and follow the "
        "playbook.",
        "",
    ]
    lines.extend(
        f"- **{skill.name}** — {skill.description}" for skill in visible
    )
    return "\n".join(lines)


def _load_root(root: Path, diagnostics: list[str]) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    if not root.is_dir():
        return skills

    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        skill_file = entry / SKILL_FILE
        if not skill_file.is_file():
            continue
        try:
            skill = _load_skill(entry, skill_file, diagnostics)
        except (OSError, UnicodeError) as exc:
            diagnostics.append(f"{skill_file}: {exc}")
            continue
        if skill is not None:
            skills[skill.name] = skill
    return skills


def _load_skill(
    dir_path: Path,
    skill_file: Path,
    diagnostics: list[str],
) -> Skill | None:
    frontmatter, body = _parse_frontmatter(
        skill_file.read_text(encoding="utf-8")
    )
    raw_name = frontmatter.get("name") or dir_path.name
    name = raw_name.strip()
    description = (frontmatter.get("description") or "").strip()

    if name != dir_path.name:
        diagnostics.append(
            f"{skill_file}: name '{name}' must match directory "
            f"'{dir_path.name}'"
        )
        return None
    if len(name) > 64 or not _NAME_PATTERN.match(name):
        diagnostics.append(
            f"{skill_file}: invalid skill name '{name}'"
        )
        return None
    if not description:
        diagnostics.append(f"{skill_file}: description is required")
        return None
    if len(description) > 1024:
        diagnostics.append(
            f"{skill_file}: description exceeds 1024 characters"
        )
        return None

    flag = frontmatter.get("disable-model-invocation", "").lower()
    return Skill(
        name=name,
        description=description,
        body=body,
        dir_path=dir_path.resolve(),
        disable_model_invocation=flag in ("true", "yes"),
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()

    closing = normalized.find("\n---", 4)
    if closing == -1:
        return {}, normalized.strip()

    frontmatter: dict[str, str] = {}
    for line in normalized[4:closing].split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key not in _KNOWN_FIELDS:
            continue
        frontmatter[key] = _unquote(value.strip())

    body = normalized[closing + 4 :].lstrip("\n").strip()
    return frontmatter, body


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


__all__ = [
    "SKILL_FILE",
    "Skill",
    "SkillLoadResult",
    "format_skills_index",
    "load_skills",
    "workspace_skills_root",
]

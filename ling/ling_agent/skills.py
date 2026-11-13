"""Skill discovery, frontmatter parsing, and prompt formatting."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SKILL_FILE = "SKILL.md"
SOURCE_LING = "ling"
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KNOWN_FIELDS = (
    "name",
    "description",
    "source",
    "disable-model-invocation",
)
_BLOCK_SCALAR_MARKERS = (">", ">-", ">+", "|", "|-", "|+")


@dataclass(slots=True, frozen=True)
class Skill:
    name: str
    description: str
    body: str
    dir_path: Path
    disable_model_invocation: bool = False
    source: str | None = None


@dataclass(slots=True, frozen=True)
class SkillLoadResult:
    skills: tuple[Skill, ...]
    diagnostics: tuple[str, ...]


def workspace_skills_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / ".agents" / "skills"


def global_skills_root() -> Path:
    override = os.environ.get("LING_GLOBAL_SKILLS_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agents" / "skills"


def load_skills(
    builtin_root: str | Path | None,
    workspace_root: str | Path,
    *,
    global_root: str | Path | None = None,
) -> SkillLoadResult:
    """Load skills with builtin > user-global > workspace precedence.

    Bundled skills are trusted and cannot be shadowed by a same-name skill
    from the global library or the workspace. Global entries tagged
    ``source: ling`` are managed mirrors of the bundled skills and are not
    loaded separately.
    """
    diagnostics: list[str] = []
    builtin = (
        _load_root(Path(builtin_root), diagnostics)
        if builtin_root is not None
        else {}
    )
    global_skills = (
        _load_root(Path(global_root), diagnostics)
        if global_root is not None
        else {}
    )
    workspace = _load_root(workspace_skills_root(workspace_root), diagnostics)

    merged = dict(builtin)
    for name, skill in global_skills.items():
        if skill.source == SOURCE_LING:
            continue
        if name not in merged:
            merged[name] = skill
    for name, skill in workspace.items():
        if name not in merged:
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


def read_skill_source(skill_file: str | Path) -> str | None:
    """Return the ``source`` frontmatter value for a SKILL.md file."""
    path = Path(skill_file)
    try:
        frontmatter, _ = _parse_frontmatter(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError):
        return None
    return (frontmatter.get("source") or "").strip() or None


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
    source = (frontmatter.get("source") or "").strip() or None
    return Skill(
        name=name,
        description=description,
        body=body,
        dir_path=dir_path.resolve(),
        disable_model_invocation=flag in ("true", "yes"),
        source=source,
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()

    closing = normalized.find("\n---", 4)
    if closing == -1:
        return {}, normalized.strip()

    lines = normalized[4:closing].split("\n")
    frontmatter: dict[str, str] = {}
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        if (
            not raw
            or raw[0] in (" ", "\t")
            or raw.lstrip().startswith("#")
            or ":" not in raw
        ):
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        if key not in _KNOWN_FIELDS:
            continue
        value = value.strip()
        if value in _BLOCK_SCALAR_MARKERS:
            block, index = _read_block(lines, index)
            frontmatter[key] = _fold_block(
                block,
                literal=value.startswith("|"),
            )
        elif _needs_continuation(value):
            parts = [value]
            while index < len(lines):
                parts.append(lines[index].strip())
                index += 1
                if _quote_closed(" ".join(parts)):
                    break
            frontmatter[key] = _unquote(" ".join(parts))
        else:
            frontmatter[key] = _unquote(value)

    body = normalized[closing + 4 :].lstrip("\n").strip()
    return frontmatter, body


def _read_block(
    lines: list[str],
    index: int,
) -> tuple[list[str], int]:
    block: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line and not line[0].isspace():
            break
        block.append(line)
        index += 1
    return block, index


def _fold_block(block: list[str], *, literal: bool) -> str:
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    if not block:
        return ""
    indent = len(block[0]) - len(block[0].lstrip())
    stripped = [
        line[indent:] if len(line) >= indent else line
        for line in block
    ]
    if literal:
        return "\n".join(stripped).strip()
    return " ".join(
        part.strip() for part in stripped if part.strip()
    )


def _needs_continuation(value: str) -> bool:
    return (
        len(value) >= 1
        and value[0] in ("'", '"')
        and not _quote_closed(value)
    )


def _quote_closed(value: str) -> bool:
    return (
        len(value) >= 2
        and value[-1] == value[0]
        and value[0] in ("'", '"')
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


__all__ = [
    "SKILL_FILE",
    "SOURCE_LING",
    "Skill",
    "SkillLoadResult",
    "format_skills_index",
    "global_skills_root",
    "load_skills",
    "read_skill_source",
    "workspace_skills_root",
]

"""Load durable, scoped project memories from ``.agents/memory``."""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MEMORY_DIRECTORY = ".agents/memory"
MAX_MEMORY_CHARS = 12_000
MEMORY_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(slots=True, frozen=True)
class ProjectMemory:
    name: str
    description: str
    body: str
    path: Path
    relative_path: str
    scope: Literal["global", "workspace"]
    always_apply: bool
    globs: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class MemoryLoadResult:
    memories: tuple[ProjectMemory, ...]
    diagnostics: tuple[str, ...]


def workspace_memory_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / MEMORY_DIRECTORY


def default_global_memory_root() -> Path:
    override = os.environ.get("LING_GLOBAL_MEMORY_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "Ling" / "memory"
    return Path.home() / ".agents" / "memory"


def load_memories(
    workspace_root: str | Path,
    *,
    global_root: str | Path | None = None,
    active_paths: tuple[str, ...] = (),
) -> MemoryLoadResult:
    inventory = inspect_memories(
        workspace_root,
        global_root=global_root,
    )
    workspace = Path(workspace_root).resolve()
    active = tuple(
        memory
        for memory in inventory.memories
        if _memory_is_active(memory, workspace, active_paths)
    )
    return MemoryLoadResult(
        memories=active,
        diagnostics=inventory.diagnostics,
    )


def inspect_memories(
    workspace_root: str | Path,
    *,
    global_root: str | Path | None = None,
) -> MemoryLoadResult:
    workspace = Path(workspace_root).resolve()
    roots: list[tuple[Literal["global", "workspace"], Path]] = []
    if global_root is not None:
        roots.append(("global", Path(global_root).expanduser().resolve()))
    roots.append(("workspace", workspace_memory_root(workspace).resolve()))

    loaded: list[ProjectMemory] = []
    diagnostics: list[str] = []
    for scope, root in roots:
        root_memories, root_diagnostics = _load_root(root, scope, workspace)
        loaded.extend(root_memories)
        diagnostics.extend(root_diagnostics)

    memories: dict[str, ProjectMemory] = {}
    body_keys: dict[str, str] = {}
    for memory in loaded:
        previous = memories.get(memory.name)
        if previous is not None:
            if previous.scope == memory.scope:
                diagnostics.append(
                    f"{memory.relative_path}: duplicate memory name "
                    f"'{memory.name}'"
                )
                continue
            diagnostics.append(
                f"{memory.relative_path}: workspace memory overrides "
                f"global memory '{memory.name}'"
            )
        body_key = " ".join(memory.body.lower().split())
        duplicate = body_keys.get(body_key)
        if duplicate is not None:
            diagnostics.append(
                f"{memory.relative_path}: duplicate memory content "
                f"matches '{duplicate}'"
            )
            continue
        memories[memory.name] = memory
        body_keys[body_key] = memory.name

    return MemoryLoadResult(
        memories=tuple(memories[name] for name in sorted(memories)),
        diagnostics=tuple(diagnostics),
    )


def _load_root(
    root: Path,
    scope: Literal["global", "workspace"],
    workspace_root: Path,
) -> tuple[list[ProjectMemory], list[str]]:
    if not root.is_dir():
        return [], []

    memories: list[ProjectMemory] = []
    diagnostics: list[str] = []
    for path in sorted(root.glob("*.md"), key=lambda item: item.name):
        if path.name.startswith("."):
            continue
        try:
            memory = _load_memory(
                path,
                scope=scope,
                workspace_root=workspace_root,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            diagnostics.append(f"{path}: {exc}")
            continue
        if memory is not None:
            memories.append(memory)
    return memories, diagnostics


def format_memories_for_prompt(
    memories: tuple[ProjectMemory, ...],
) -> str:
    if not memories:
        return ""
    sections = [
        "## Project memory",
        "",
        "These durable project memories apply automatically. Follow them "
        "unless they conflict with system safety or AGENTS.md.",
    ]
    for memory in memories:
        sections.extend(
            [
                "",
                f"### {memory.name}",
                f"Scope: {memory.scope}",
                f"Source: {memory.relative_path}",
                f"Description: {memory.description}",
                "",
                memory.body,
            ]
        )
    return "\n".join(sections)


def _load_memory(
    path: Path,
    *,
    scope: Literal["global", "workspace"],
    workspace_root: Path,
) -> ProjectMemory | None:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)
    body = body.strip()
    if not body:
        raise ValueError("memory body must not be empty")
    if len(body) > MAX_MEMORY_CHARS:
        raise ValueError(
            f"memory body must be at most {MAX_MEMORY_CHARS} characters"
        )
    name = (frontmatter.get("name") or path.stem).strip()
    description = (frontmatter.get("description") or "").strip()
    if not name:
        raise ValueError("memory name must not be empty")
    if not description:
        raise ValueError("memory description is required")
    always_apply = frontmatter.get(
        "alwaysApply", ""
    ).strip().lower() in ("true", "yes")
    globs = _parse_globs(frontmatter.get("globs", ""))
    relative_path = (
        path.relative_to(workspace_root).as_posix()
        if scope == "workspace"
        else str(path.resolve())
    )
    return ProjectMemory(
        name=name,
        description=description,
        body=body,
        path=path.resolve(),
        relative_path=relative_path,
        scope=scope,
        always_apply=always_apply,
        globs=globs,
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized
    closing = normalized.find("\n---", 4)
    if closing < 0:
        return {}, normalized

    fields: dict[str, str] = {}
    for line in normalized[4:closing].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key in {"name", "description", "alwaysApply", "globs"}:
            fields[key] = _unquote(value.strip())
    return fields, normalized[closing + 4 :]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_globs(value: str) -> tuple[str, ...]:
    text = value.strip()
    if not text:
        return ()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = [
                item.strip().strip("'\"")
                for item in text[1:-1].split(",")
            ]
    else:
        parsed = [item.strip().strip("'\"") for item in text.split(",")]
    if not isinstance(parsed, list):
        return ()
    return tuple(
        item
        for item in parsed
        if isinstance(item, str) and item.strip()
    )


def _memory_is_active(
    memory: ProjectMemory,
    workspace_root: Path,
    active_paths: tuple[str, ...],
) -> bool:
    if memory.always_apply:
        return True
    if not memory.globs or not active_paths:
        return False
    relative_paths: list[str] = []
    for value in active_paths:
        path = Path(value)
        try:
            resolved = (
                path.resolve()
                if path.is_absolute()
                else (workspace_root / path).resolve()
            )
            relative_paths.append(
                resolved.relative_to(workspace_root).as_posix()
            )
        except (OSError, ValueError):
            relative_paths.append(path.as_posix().lstrip("./"))
    for pattern in memory.globs:
        normalized = pattern.replace("\\", "/")
        for path in relative_paths:
            if fnmatch.fnmatchcase(path, normalized):
                return True
            if normalized.startswith("**/") and fnmatch.fnmatchcase(
                path.rsplit("/", 1)[-1],
                normalized[3:],
            ):
                return True
    return False


class MemoryStore:
    """Create, update, and delete project/global memory files."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        global_root: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.global_root = (
            Path(global_root).expanduser().resolve()
            if global_root is not None
            else None
        )

    def list(self) -> MemoryLoadResult:
        return inspect_memories(
            self.workspace_root,
            global_root=self.global_root,
        )

    def write(
        self,
        *,
        name: str,
        description: str,
        content: str,
        scope: Literal["global", "workspace"],
        always_apply: bool = True,
        globs: tuple[str, ...] = (),
    ) -> Path:
        self._validate(name, description, content)
        root = self._root(scope)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.md"
        globs_text = (
            json.dumps(list(globs), ensure_ascii=False)
            if globs
            else "[]"
        )
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"name: {name}",
                    f"description: {description}",
                    f"alwaysApply: {'true' if always_apply else 'false'}",
                    f"globs: {globs_text}",
                    "---",
                    "",
                    content.strip(),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def delete(
        self,
        *,
        name: str,
        scope: Literal["global", "workspace"],
    ) -> Path:
        root = self._root(scope)
        path = root / f"{name}.md"
        resolved = path.resolve()
        if root.resolve() not in resolved.parents:
            raise ValueError("memory path escapes its scope")
        if not resolved.is_file():
            raise ValueError(f"memory not found: {scope}:{name}")
        resolved.unlink()
        return resolved

    def _root(self, scope: Literal["global", "workspace"]) -> Path:
        if scope == "workspace":
            return workspace_memory_root(self.workspace_root)
        if self.global_root is None:
            raise ValueError("global memory is not configured")
        return self.global_root

    @staticmethod
    def _validate(name: str, description: str, content: str) -> None:
        if not MEMORY_NAME_PATTERN.match(name):
            raise ValueError(
                "memory name must use lowercase letters, numbers, and hyphens"
            )
        if not description.strip():
            raise ValueError("memory description is required")
        if not content.strip():
            raise ValueError("memory content is required")
        if len(content) > MAX_MEMORY_CHARS:
            raise ValueError(
                f"memory content must be at most {MAX_MEMORY_CHARS} characters"
            )


__all__ = [
    "MAX_MEMORY_CHARS",
    "MEMORY_DIRECTORY",
    "MEMORY_NAME_PATTERN",
    "MemoryLoadResult",
    "MemoryStore",
    "ProjectMemory",
    "default_global_memory_root",
    "format_memories_for_prompt",
    "inspect_memories",
    "load_memories",
    "workspace_memory_root",
]

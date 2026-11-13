"""Mirror bundled skills into the user's global skill library.

Only directories whose SKILL.md declares ``source: ling`` are managed. A
manifest records the last synced tree hash so locally modified copies are
left untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .skills import SKILL_FILE, SOURCE_LING, read_skill_source

MANIFEST_NAME = ".ling-skills.json"
_IGNORED_PARTS = frozenset({"__pycache__", ".git", ".DS_Store"})
_IGNORED_SUFFIXES = (".pyc", ".pyo")


@dataclass(slots=True)
class SkillSyncResult:
    installed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped_user: list[str] = field(default_factory=list)
    skipped_modified: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def sync_bundled_skills(
    builtin_root: str | Path | None,
    global_root: str | Path | None,
) -> SkillSyncResult:
    result = SkillSyncResult()
    if builtin_root is None or global_root is None:
        return result

    source_root = Path(builtin_root).expanduser().resolve()
    target_root = Path(global_root).expanduser().resolve()
    if not source_root.is_dir():
        return result

    manifest_path = target_root / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    managed = manifest.setdefault("skills", {})
    changed = False

    for entry in sorted(source_root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        source_file = entry / SKILL_FILE
        if not source_file.is_file():
            continue
        if read_skill_source(source_file) != SOURCE_LING:
            continue

        name = entry.name
        destination = target_root / name
        destination_file = destination / SKILL_FILE

        try:
            if destination.exists() and not destination_file.is_file():
                result.diagnostics.append(
                    f"global skill path is not a skill: {destination}"
                )
                continue

            destination_hash: str | None = None
            if destination_file.is_file():
                if read_skill_source(destination_file) != SOURCE_LING:
                    result.skipped_user.append(name)
                    continue
                destination_hash = skill_tree_hash(destination)
                previous = managed.get(name, {}).get("hash")
                if previous and destination_hash != previous:
                    result.skipped_modified.append(name)
                    result.diagnostics.append(
                        f"global skill '{name}' has local changes; "
                        "not updated"
                    )
                    continue

            source_hash = skill_tree_hash(entry)
            if destination_hash == source_hash:
                result.unchanged.append(name)
                if managed.get(name, {}).get("hash") != source_hash:
                    managed[name] = {"hash": source_hash}
                    changed = True
                continue

            target_root.mkdir(parents=True, exist_ok=True)
            existed = destination_file.is_file()
            shutil.copytree(entry, destination, dirs_exist_ok=True)
            synced_hash = skill_tree_hash(destination)
            managed[name] = {"hash": synced_hash}
            changed = True
            if existed:
                result.updated.append(name)
            else:
                result.installed.append(name)
        except OSError as exc:
            result.diagnostics.append(
                f"unable to sync global skill '{name}': {exc}"
            )

    if changed:
        manifest["version"] = 1
        try:
            _write_manifest(manifest_path, manifest)
        except OSError as exc:
            result.diagnostics.append(
                f"unable to write skill manifest: {exc}"
            )
    return result


def skill_tree_hash(directory: str | Path) -> str:
    """Hash the managed files in one skill directory."""
    root = Path(directory)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.name.startswith(".") or path.suffix in _IGNORED_SUFFIXES:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    skills = raw.get("skills")
    if not isinstance(skills, dict):
        return {}
    filtered: dict[str, dict[str, str]] = {}
    for name, value in skills.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        hashed = value.get("hash")
        if isinstance(hashed, str) and hashed:
            filtered[name] = {"hash": hashed}
    return {"version": 1, "skills": filtered}


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


__all__ = [
    "MANIFEST_NAME",
    "SkillSyncResult",
    "skill_tree_hash",
    "sync_bundled_skills",
]

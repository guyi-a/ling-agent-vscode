"""Tests for mirroring bundled skills into the global skill library."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ling_agent.skill_sync import (
    MANIFEST_NAME,
    skill_tree_hash,
    sync_bundled_skills,
)


def _write_skill(
    root: Path,
    name: str,
    *,
    body: str = "Body",
    source: str | None = "ling",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        f'description: "{name} skill"',
    ]
    if source is not None:
        lines.append(f"source: {source}")
    lines.extend(["---", "", body, ""])
    (skill_dir / "SKILL.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return skill_dir


def test_sync_installs_only_managed_skills() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        target = Path(temp) / "global"
        _write_skill(builtin, "pdf")
        _write_skill(builtin, "user-helper", source=None)

        result = sync_bundled_skills(builtin, target)

        assert result.installed == ["pdf"]
        assert (target / "pdf" / "SKILL.md").is_file()
        assert not (target / "user-helper").exists()
        manifest = json.loads(
            (target / MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["skills"]["pdf"]["hash"] == skill_tree_hash(
            target / "pdf"
        )


def test_sync_leaves_untagged_user_copy_untouched() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        target = Path(temp) / "global"
        _write_skill(builtin, "frontend-design", body="bundled")
        user = _write_skill(
            target,
            "frontend-design",
            body="user copy",
            source=None,
        )

        result = sync_bundled_skills(builtin, target)

        assert result.skipped_user == ["frontend-design"]
        assert (user / "SKILL.md").read_text(
            encoding="utf-8"
        ).endswith("user copy\n")


def test_sync_updates_unmodified_managed_skill() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        target = Path(temp) / "global"
        _write_skill(builtin, "pdf", body="v1")
        sync_bundled_skills(builtin, target)

        _write_skill(builtin, "pdf", body="v2")
        result = sync_bundled_skills(builtin, target)

        assert result.updated == ["pdf"]
        assert "v2" in (target / "pdf" / "SKILL.md").read_text(
            encoding="utf-8"
        )


def test_sync_preserves_locally_modified_managed_skill() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        target = Path(temp) / "global"
        _write_skill(builtin, "pdf", body="v1")
        sync_bundled_skills(builtin, target)

        destination = target / "pdf" / "SKILL.md"
        destination.write_text(
            destination.read_text(encoding="utf-8").replace(
                "v1", "local edit"
            ),
            encoding="utf-8",
        )
        _write_skill(builtin, "pdf", body="v2")
        result = sync_bundled_skills(builtin, target)

        assert result.skipped_modified == ["pdf"]
        assert "local edit" in destination.read_text(encoding="utf-8")
        assert any("local changes" in item for item in result.diagnostics)


def test_sync_overwrites_managed_copy_without_manifest_once() -> None:
    with tempfile.TemporaryDirectory() as temp:
        builtin = Path(temp) / "builtin"
        target = Path(temp) / "global"
        _write_skill(builtin, "pdf", body="new")
        _write_skill(target, "pdf", body="old")

        result = sync_bundled_skills(builtin, target)

        assert result.updated == ["pdf"]
        assert "new" in (target / "pdf" / "SKILL.md").read_text(
            encoding="utf-8"
        )


def test_tree_hash_ignores_pycache() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "pdf"
        _write_skill(Path(temp), "pdf")
        before = skill_tree_hash(root)
        cache = root / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(b"cache")
        assert skill_tree_hash(root) == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

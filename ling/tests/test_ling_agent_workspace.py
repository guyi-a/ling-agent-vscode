"""Tests for ling_agent workspace safety and read-only operations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ling_agent import HarnessConfig, Workspace, WorkspaceFileError, WorkspacePathError


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_workspace_path_resolution_and_escape() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        root = base / "workspace"
        root.mkdir()
        _write(root, "src/a.py", "print('a')\n")
        outside = base / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        workspace = Workspace(root)

        assert workspace.resolve("src/a.py") == (root / "src/a.py").resolve()

        for path in ("../outside.txt", str(outside)):
            try:
                workspace.resolve(path)
            except WorkspacePathError:
                pass
            else:
                raise AssertionError(f"Expected path escape rejection: {path}")

        symlink = root / "outside-link"
        try:
            os.symlink(outside, symlink)
        except (OSError, NotImplementedError):
            pass
        else:
            try:
                workspace.resolve("outside-link")
            except WorkspacePathError:
                pass
            else:
                raise AssertionError("Expected symlink escape rejection")


def test_workspace_read_list_glob_and_grep() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(root, "src/a.py", "alpha\nbeta\ngamma\n")
        _write(root, "src/b.txt", "beta outside python\n")
        _write(root, "docs/readme.md", "alpha docs\n")
        workspace = Workspace(root)

        excerpt = workspace.read_text("src/a.py", start_line=2, line_limit=1)
        assert excerpt.startswith("beta")
        assert excerpt.endswith("[truncated]")
        assert workspace.list_files("src") == ["src/a.py", "src/b.txt"]
        assert workspace.glob("**/*.py") == ["src/a.py"]
        assert workspace.grep("alpha", max_matches=10) == [
            "docs/readme.md:1:alpha docs",
            "src/a.py:1:alpha",
        ]

        try:
            workspace.read_text("missing.txt")
        except WorkspaceFileError:
            pass
        else:
            raise AssertionError("Expected missing file error")


def test_workspace_rejects_binary_text_reads() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "doc.pdf").write_bytes(b"%PDF-1.4\x00\x01binary")
        workspace = Workspace(root)

        try:
            workspace.read_text("doc.pdf")
        except WorkspaceFileError:
            pass
        else:
            raise AssertionError("Expected binary file rejection")


def test_workspace_output_bounds() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(root, "long.txt", "abcdef\nsecond\nthird\n")
        workspace = Workspace(
            root,
            HarnessConfig(max_read_chars=3, max_read_lines=2),
        )

        result = workspace.read_text("long.txt")

        assert result.startswith("abc")
        assert result.endswith("[truncated]")


if __name__ == "__main__":
    test_workspace_path_resolution_and_escape()
    test_workspace_read_list_glob_and_grep()
    test_workspace_output_bounds()
    print("all ling_agent workspace tests passed")

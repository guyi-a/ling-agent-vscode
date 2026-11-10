"""Safe workspace access for ling_agent."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Sequence

from ling_ai.types import ImageContent

from .config import HarnessConfig

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


class WorkspaceError(RuntimeError):
    pass


class WorkspacePathError(WorkspaceError):
    pass


class WorkspaceFileError(WorkspaceError):
    pass


class Workspace:
    """A workspace root with safe path resolution and bounded read operations."""

    def __init__(
        self,
        root: str | Path,
        config: HarnessConfig | None = None,
    ) -> None:
        resolved = Path(root).resolve()
        if not resolved.exists():
            raise WorkspaceError(f"Workspace does not exist: {resolved}")
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {resolved}")

        self._root = resolved
        self._config = config or HarnessConfig()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config(self) -> HarnessConfig:
        return self._config

    def resolve(self, path: str | Path) -> Path:
        user_path = Path(path)
        if user_path.is_absolute():
            raise WorkspacePathError(f"Absolute paths are not allowed: {path}")

        candidate = (self._root / user_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise WorkspacePathError(
                f"Path escapes workspace: {path}"
            ) from None
        return candidate

    def read_text(
        self,
        path: str | Path,
        *,
        start_line: int = 1,
        line_limit: int | None = None,
    ) -> str:
        return self._read_resolved_text(
            self.resolve(path),
            label=str(path),
            start_line=start_line,
            line_limit=line_limit,
        )

    def read_absolute_text(
        self,
        path: str | Path,
        *,
        allowed_roots: Sequence[Path] = (),
        start_line: int = 1,
        line_limit: int | None = None,
    ) -> str:
        """Read an absolute file inside the workspace or an allowed root."""
        candidate = Path(path)
        if not candidate.is_absolute():
            raise WorkspacePathError(
                f"Expected an absolute path, got: {path}"
            )
        resolved = candidate.expanduser().resolve()
        if not (
            self._is_inside(resolved)
            or any(
                _is_relative_to(resolved, root.expanduser().resolve())
                for root in allowed_roots
            )
        ):
            raise WorkspacePathError(
                f"Absolute path is outside allowed roots: {path}"
            )
        return self._read_resolved_text(
            resolved,
            label=str(path),
            start_line=start_line,
            line_limit=line_limit,
        )

    def _read_resolved_text(
        self,
        resolved: Path,
        *,
        label: str,
        start_line: int,
        line_limit: int | None,
    ) -> str:
        if start_line < 1:
            raise ValueError("start_line must be at least 1")
        if not resolved.exists():
            raise WorkspaceFileError(f"File does not exist: {label}")
        if not resolved.is_file():
            raise WorkspaceFileError(f"Path is not a file: {label}")

        limit = min(
            line_limit or self._config.max_read_lines,
            self._config.max_read_lines,
        )
        byte_limit = self._config.max_file_bytes
        with resolved.open("rb") as handle:
            data = handle.read(byte_limit + 1)
        if b"\x00" in data[:byte_limit]:
            raise WorkspaceFileError(
                "File appears to be binary and cannot be read as text: "
                f"{label}. Use an appropriate conversion tool or command."
            )

        bytes_truncated = len(data) > byte_limit
        text = data[:byte_limit].decode("utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[start_line - 1 : start_line - 1 + limit]
        result = "\n".join(selected)

        line_truncated = start_line - 1 + len(selected) < len(lines)
        char_truncated = len(result) > self._config.max_read_chars
        if char_truncated:
            result = result[: self._config.max_read_chars]

        if bytes_truncated or line_truncated or char_truncated:
            result += "\n\n[truncated]"
        return result

    def read_image(self, path: str | Path) -> ImageContent:
        resolved = self.resolve(path)
        if not resolved.exists():
            raise WorkspaceFileError(f"File does not exist: {path}")
        if not resolved.is_file():
            raise WorkspaceFileError(f"Path is not a file: {path}")

        mime_type = _IMAGE_MIME_TYPES.get(resolved.suffix.lower())
        if mime_type is None:
            raise WorkspaceFileError(f"Unsupported image type: {path}")

        size = resolved.stat().st_size
        if size < 1:
            raise WorkspaceFileError(f"Image is empty: {path}")
        if size > self._config.max_attachment_image_bytes:
            raise WorkspaceFileError(
                "Image exceeds max_attachment_image_bytes "
                f"({self._config.max_attachment_image_bytes})"
            )

        data = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return ImageContent(data=data, mime_type=mime_type)

    def list_files(
        self,
        path: str | Path = ".",
        *,
        recursive: bool = False,
    ) -> list[str]:
        resolved = self.resolve(path)
        if not resolved.exists():
            raise WorkspaceFileError(f"Directory does not exist: {path}")
        if not resolved.is_dir():
            raise WorkspaceFileError(f"Path is not a directory: {path}")

        entries = resolved.rglob("*") if recursive else resolved.iterdir()
        results = sorted(
            self._relative(item)
            for item in entries
            if self._is_inside(item)
        )
        return results[: self._config.max_list_entries]

    def glob(self, pattern: str) -> list[str]:
        if Path(pattern).is_absolute():
            raise WorkspacePathError("Absolute glob patterns are not allowed")

        matches = {
            self._relative(path)
            for path in self._root.glob(pattern)
            if self._is_inside(path)
        }
        return sorted(matches)[: self._config.max_list_entries]

    def grep(
        self,
        pattern: str,
        path: str | Path = ".",
        *,
        max_matches: int | None = None,
    ) -> list[str]:
        limit = min(
            max_matches or self._config.max_grep_matches,
            self._config.max_grep_matches,
        )
        target = self.resolve(path)
        if not target.exists():
            raise WorkspaceFileError(f"Path does not exist: {path}")

        expression = re.compile(pattern)
        files = [target] if target.is_file() else target.rglob("*")
        matches: list[str] = []
        total_chars = 0
        truncated = False
        max_chars = self._config.max_tool_result_chars

        for candidate in sorted(files, key=lambda item: item.as_posix()):
            if len(matches) >= limit or truncated:
                break
            if not candidate.is_file() or not self._is_inside(candidate):
                continue
            try:
                data = candidate.read_bytes()[: self._config.max_file_bytes]
            except OSError:
                continue
            if b"\x00" in data:
                continue

            relative = self._relative(candidate)
            text = data.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if expression.search(line):
                    entry = f"{relative}:{line_number}:{line}"
                    remaining = max_chars - total_chars
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(entry) > remaining:
                        entry = f"{entry[: max(0, remaining - 1)]}…"
                        truncated = True
                    matches.append(entry)
                    total_chars += len(entry) + 1
                    if len(matches) >= limit or truncated:
                        break

        if truncated:
            matches.append("[truncated]")
        return matches

    def write_text(self, path: str | Path, content: str) -> str:
        resolved = self.resolve(path)
        if resolved.exists() and resolved.is_dir():
            raise WorkspaceFileError(f"Path is a directory: {path}")

        encoded = content.encode("utf-8")
        if len(encoded) > self._config.max_file_bytes:
            raise WorkspaceFileError(
                f"Content exceeds max_file_bytes ({self._config.max_file_bytes})"
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(encoded)
        return f"Wrote {len(encoded)} bytes to {self._relative(resolved)}"

    def replace_text(
        self,
        path: str | Path,
        old_text: str,
        new_text: str,
        *,
        count: int = 1,
    ) -> str:
        if count < 1:
            raise ValueError("count must be at least 1")

        resolved = self.resolve(path)
        if not resolved.exists():
            raise WorkspaceFileError(f"File does not exist: {path}")
        if not resolved.is_file():
            raise WorkspaceFileError(f"Path is not a file: {path}")
        if resolved.stat().st_size > self._config.max_file_bytes:
            raise WorkspaceFileError(
                f"File exceeds max_file_bytes ({self._config.max_file_bytes})"
            )

        text = resolved.read_text(encoding="utf-8")
        if old_text not in text:
            raise WorkspaceFileError(f"Text not found in {path}")
        updated = text.replace(old_text, new_text, count)
        encoded = updated.encode("utf-8")
        if len(encoded) > self._config.max_file_bytes:
            raise WorkspaceFileError(
                f"Updated content exceeds max_file_bytes "
                f"({self._config.max_file_bytes})"
            )

        resolved.write_bytes(encoded)
        return f"Edited {self._relative(resolved)}"

    def _is_inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._root)
        except ValueError:
            return False
        return True

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        return resolved.relative_to(self._root).as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "Workspace",
    "WorkspaceError",
    "WorkspaceFileError",
    "WorkspacePathError",
]

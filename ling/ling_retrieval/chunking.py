"""Chunk Python source files for retrieval experiments."""

from __future__ import annotations

import ast
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ling_agent.workspace import (
    DEFAULT_IGNORED_DIRECTORIES,
    DEFAULT_IGNORED_PATHS,
)

SUFFIX_LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}
SOURCE_SUFFIXES = frozenset(SUFFIX_LANGUAGES)
MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class Chunk:
    path: str
    language: str
    kind: str
    symbol: str
    start_line: int
    end_line: int
    signature: str
    comment: str
    body: str

    @property
    def chunk_id(self) -> str:
        return f"{self.path}:{self.start_line}:{self.symbol}"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


def iter_source_files(root: Path) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    for current, directories, filenames in os.walk(root):
        current_path = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if not _is_ignored(
                current_path / name,
                root,
                is_directory=True,
            )
        )
        for name in sorted(filenames):
            path = current_path / name
            if path.suffix not in SOURCE_SUFFIXES:
                continue
            if _is_ignored(path, root, is_directory=False):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if 0 < size <= MAX_FILE_BYTES:
                files.append(path)
    return files


def chunk_files(
    root: Path,
    files: list[Path] | None = None,
) -> list[Chunk]:
    root = root.resolve()
    selected = files if files is not None else iter_source_files(root)
    chunks: list[Chunk] = []
    for path in selected:
        chunks.extend(chunk_file(path, root))
    return chunks


def chunk_file(path: Path, root: Path) -> list[Chunk]:
    language = SUFFIX_LANGUAGES.get(path.suffix)
    if language is None:
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return []

    if language == "python":
        return _chunk_python(source, relative)
    return _chunk_typescript(source, relative, language)


def _chunk_python(source: str, relative: str) -> list[Chunk]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    chunks: list[Chunk] = []
    for node, kind in _python_nodes(tree):
        start_line = _start_line(node)
        end_line = node.end_lineno or node.lineno
        chunks.append(
            Chunk(
                path=relative,
                language="python",
                kind=kind,
                symbol=node.name,
                start_line=start_line,
                end_line=end_line,
                signature=_signature(node),
                comment=_comment(node, lines, start_line),
                body="\n".join(lines[start_line - 1 : end_line]),
            )
        )
    return chunks


def _python_nodes(
    tree: ast.Module,
) -> Iterator[tuple[ast.AST, str]]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, "function"
        elif isinstance(node, ast.ClassDef):
            yield node, "class"
            for child in node.body:
                if isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    yield child, "method"


def _start_line(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> int:
    lines = [node.lineno]
    lines.extend(
        decorator.lineno for decorator in node.decorator_list
    )
    return min(lines)


def _signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(
            ast.unparse(base) for base in node.bases
        )
        suffix = f"({bases})" if bases else ""
        return f"class {node.name}{suffix}"
    prefix = (
        "async def"
        if isinstance(node, ast.AsyncFunctionDef)
        else "def"
    )
    returns = (
        f" -> {ast.unparse(node.returns)}"
        if node.returns is not None
        else ""
    )
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _comment(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    lines: list[str],
    start_line: int,
) -> str:
    parts: list[str] = []
    index = start_line - 2
    while index >= 0:
        text = lines[index].strip()
        if not text or not text.startswith("#"):
            break
        parts.append(text)
        index -= 1
    parts.reverse()
    docstring = ast.get_docstring(node, clean=False)
    if docstring:
        parts.append(docstring)
    return "\n".join(parts).strip()


_TS_CLASS = re.compile(
    r"^(?P<indent>[ 	]*)(?:export\s+)?(?:default\s+)?"
    r"(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_FUNCTION = re.compile(
    r"^(?P<indent>[ 	]*)(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_ARROW = re.compile(
    r"^(?P<indent>[ 	]*)(?:export\s+)?(?:const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*"
    r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.MULTILINE,
)
_TS_FUNCTION_CONST = re.compile(
    r"^(?P<indent>[ 	]*)(?:export\s+)?(?:const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*"
    r"(?:async\s*)?function\b",
    re.MULTILINE,
)
_TS_MEMBER = re.compile(
    r"^(?P<indent>[ \t]+)"
    r"(?:(?:public|private|protected|static|async|readonly|"
    r"override|get|set)\s+)*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?:<[^>\n]*>)?\s*\("
)


def _chunk_typescript(
    source: str,
    relative: str,
    language: str,
) -> list[Chunk]:
    lines = source.splitlines()
    chunks: list[Chunk] = []
    class_ranges: list[tuple[int, int]] = []

    for match in _TS_CLASS.finditer(source):
        body_start, body_end = _ts_body_range(source, match.start())
        if body_start is None or body_end is None:
            continue
        chunks.append(
            _ts_chunk(
                source,
                lines,
                relative,
                language,
                kind="class",
                name=match.group("name"),
                decl_start=match.start(),
                body_start=body_start,
                body_end=body_end,
            )
        )
        class_ranges.append(
            (body_start, body_end)
        )

    for regex, kind in (
        (_TS_FUNCTION, "function"),
        (_TS_ARROW, "function"),
        (_TS_FUNCTION_CONST, "function"),
    ):
        for match in regex.finditer(source):
            if match.group("indent"):
                continue
            body_start, body_end = _ts_body_range(
                source,
                match.start(),
            )
            if body_start is None or body_end is None:
                continue
            chunks.append(
                _ts_chunk(
                    source,
                    lines,
                    relative,
                    language,
                    kind=kind,
                    name=match.group("name"),
                    decl_start=match.start(),
                    body_start=body_start,
                    body_end=body_end,
                )
            )

    for class_start, class_end in class_ranges:
        class_indent = _line_indent(
            lines,
            source[:class_start].count("\n"),
        )
        for decl_start, name in _class_member_matches(
            source,
            class_start,
            class_end,
            class_indent,
        ):
            body_start, body_end = _ts_body_range(
                source,
                decl_start,
            )
            if body_start is None or body_end is None:
                continue
            chunks.append(
                _ts_chunk(
                    source,
                    lines,
                    relative,
                    language,
                    kind="method",
                    name=name,
                    decl_start=decl_start,
                    body_start=body_start,
                    body_end=body_end,
                )
            )

    chunks.sort(key=lambda chunk: (chunk.start_line, chunk.symbol))
    return chunks


def _class_member_matches(
    source: str,
    class_start: int,
    class_end: int,
    class_indent: int,
) -> list[tuple[int, str]]:
    body = source[class_start : class_end + 1]
    candidates: list[tuple[int, str, int]] = []
    offset = 0
    for line in body.splitlines(keepends=True):
        line_text = line.rstrip("\r\n")
        match = _TS_MEMBER.match(line_text)
        if match is not None:
            indent = len(match.group("indent"))
            if indent > class_indent:
                candidates.append(
                    (
                        class_start + offset,
                        match.group("name"),
                        indent,
                    )
                )
        offset += len(line)
    if not candidates:
        return []
    member_indent = min(indent for _, _, indent in candidates)
    return [
        (start, name)
        for start, name, indent in candidates
        if indent == member_indent
    ]


def _line_indent(lines: list[str], line_index: int) -> int:
    if line_index < 0 or line_index >= len(lines):
        return 0
    line = lines[line_index]
    return len(line) - len(line.lstrip())


def _ts_chunk(
    source: str,
    lines: list[str],
    relative: str,
    language: str,
    *,
    kind: str,
    name: str,
    decl_start: int,
    body_start: int,
    body_end: int,
) -> Chunk:
    decl_line = source[:decl_start].count("\n")
    end_line = source[:body_end].count("\n")
    jsdoc_start = _jsdoc_start(lines, decl_line)
    start_line = jsdoc_start if jsdoc_start is not None else decl_line
    signature = re.sub(
        r"\s+",
        " ",
        source[decl_start:body_start],
    ).strip()
    comment = (
        "\n".join(lines[jsdoc_start:decl_line])
        if jsdoc_start is not None
        else ""
    )
    return Chunk(
        path=relative,
        language=language,
        kind=kind,
        symbol=name,
        start_line=start_line + 1,
        end_line=end_line + 1,
        signature=signature,
        comment=comment,
        body="\n".join(lines[start_line : end_line + 1]),
    )


def _jsdoc_start(
    lines: list[str],
    decl_line: int,
) -> int | None:
    index = decl_line - 1
    while index >= 0 and lines[index].strip().startswith("@"):
        index -= 1
    if index < 0 or not lines[index].strip().endswith("*/"):
        return None
    for start in range(index, -1, -1):
        text = lines[start].strip()
        if text.startswith("/**"):
            return start
        if text.startswith("/*"):
            return None
    return None


def _ts_body_range(
    source: str,
    start: int,
) -> tuple[int | None, int | None]:
    depth = 0
    index = start
    while index < len(source):
        char = source[index]
        if char in "'\"`":
            index = _skip_js_string(source, index)
            continue
        if (
            char == "/"
            and index + 1 < len(source)
            and source[index + 1] in "/*"
        ):
            index = _skip_js_comment(source, index)
            continue
        if depth == 0 and source.startswith("=>", index):
            next_index = index + 2
            while (
                next_index < len(source)
                and source[next_index].isspace()
            ):
                next_index += 1
            if (
                next_index < len(source)
                and source[next_index] == "{"
            ):
                close = _match_brace(source, next_index)
                return (
                    next_index,
                    close if close is not None else len(source) - 1,
                )
            return (
                next_index,
                _statement_end(source, next_index),
            )
        if char == "{" and depth == 0:
            close = _match_brace(source, index)
            return (
                index,
                close if close is not None else len(source) - 1,
            )
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        index += 1
    return None, None


def _match_brace(source: str, open_index: int) -> int | None:
    depth = 0
    index = open_index
    while index < len(source):
        char = source[index]
        if char in "'\"`":
            index = _skip_js_string(source, index)
            continue
        if (
            char == "/"
            and index + 1 < len(source)
            and source[index + 1] in "/*"
        ):
            index = _skip_js_comment(source, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _statement_end(source: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(source):
        char = source[index]
        if char in "'\"`":
            index = _skip_js_string(source, index)
            continue
        if (
            char == "/"
            and index + 1 < len(source)
            and source[index + 1] in "/*"
        ):
            index = _skip_js_comment(source, index)
            continue
        if char == ";" and depth == 0:
            return index
        if char == "\n" and depth == 0:
            return max(start, index - 1)
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        index += 1
    return len(source) - 1


def _skip_js_string(source: str, start: int) -> int:
    quote = source[start]
    index = start + 1
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return len(source)


def _skip_js_comment(source: str, start: int) -> int:
    if source.startswith("//", start):
        end = source.find("\n", start)
        return len(source) if end < 0 else end + 1
    end = source.find("*/", start + 2)
    return len(source) if end < 0 else end + 2


def _is_ignored(
    path: Path,
    root: Path,
    *,
    is_directory: bool,
) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    parts = relative.parts if is_directory else relative.parts[:-1]
    if any(part in DEFAULT_IGNORED_DIRECTORIES for part in parts):
        return True
    relative_path = relative.as_posix()
    return any(
        relative_path == ignored
        or relative_path.startswith(f"{ignored}/")
        for ignored in DEFAULT_IGNORED_PATHS
    )


__all__ = [
    "Chunk",
    "chunk_file",
    "chunk_files",
    "iter_source_files",
]

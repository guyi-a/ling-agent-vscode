"""Comment targets and language-aware target analysis."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass


class CommentError(Exception):
    """A comment request that cannot be completed."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True, frozen=True)
class CommentTarget:
    path: str
    language_id: str
    symbol_name: str
    symbol_kind: str
    # Zero-based line where `source_text` starts in the document.
    start_line: int
    source_text: str
    document_version: int = 0
    content_hash: str = ""
    class_context: str | None = None


@dataclass(slots=True, frozen=True)
class Parameter:
    name: str
    annotation: str | None = None
    default: str | None = None


@dataclass(slots=True, frozen=True)
class Placement:
    # Zero-based line where the comment is inserted or replaced.
    line: int
    # Whitespace prefix applied to every inserted line.
    indent: str
    # Exclusive zero-based end line when replacing an existing comment.
    end_line: int | None = None

    @property
    def replace(self) -> bool:
        return self.end_line is not None


@dataclass(slots=True, frozen=True)
class TargetAnalysis:
    signature: str
    parameters: tuple[Parameter, ...]
    returns: str | None
    raises: tuple[str, ...]
    decorators: tuple[str, ...]
    class_context: str | None
    existing_comment: str | None
    placement: Placement


SymbolNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef

PYTHON_LANGUAGES = frozenset({"python"})
JS_LANGUAGES = frozenset(
    {
        "typescript",
        "javascript",
        "typescriptreact",
        "javascriptreact",
    }
)


def analyze_target(target: CommentTarget) -> TargetAnalysis:
    """Extract structured context used by prompts and checks."""

    if target.language_id in PYTHON_LANGUAGES:
        return _analyze_python(target)
    if target.language_id in JS_LANGUAGES:
        return _analyze_typescript(target)
    raise CommentError(
        "unsupported_language",
        f"Comment generation does not support {target.language_id}.",
    )


def _analyze_python(target: CommentTarget) -> TargetAnalysis:
    if not target.source_text.strip():
        raise CommentError("invalid_target", "The selected symbol is empty.")

    indent = _common_indent(target.source_text)
    dedented = _dedent(target.source_text, indent)
    try:
        tree = ast.parse(dedented)
    except SyntaxError as exc:
        raise CommentError(
            "invalid_target",
            f"Unable to parse the selected code: {exc.msg}",
        ) from exc

    node = _find_symbol(tree, target.symbol_name)
    if node is None:
        raise CommentError(
            "invalid_target",
            f"Symbol '{target.symbol_name}' was not found in the selection.",
        )

    existing = ast.get_docstring(node, clean=False)
    if isinstance(node, ast.ClassDef):
        parameters: tuple[Parameter, ...] = ()
        returns = None
    else:
        parameters = _parameters(node)
        returns = _annotation(node.returns)

    return TargetAnalysis(
        signature=_signature(node),
        parameters=parameters,
        returns=returns,
        raises=_raises(node),
        decorators=tuple(
            ast.unparse(decorator) for decorator in node.decorator_list
        ),
        class_context=target.class_context,
        existing_comment=existing,
        placement=_python_placement(
            node,
            target,
            indent,
            replace=existing is not None,
        ),
    )


def _common_indent(text: str) -> str:
    prefixes = [
        line[: len(line) - len(line.lstrip())]
        for line in text.splitlines()
        if line.strip()
    ]
    if not prefixes:
        return ""
    common = prefixes[0]
    for candidate in prefixes[1:]:
        while not candidate.startswith(common):
            common = common[:-1]
    return common


def _dedent(text: str, indent: str) -> str:
    if not indent:
        return text
    lines = []
    for line in text.splitlines():
        lines.append(line[len(indent) :] if line.startswith(indent) else line)
    return "\n".join(lines)


def _find_symbol(tree: ast.Module, name: str) -> SymbolNode | None:
    candidates = [
        node
        for node in tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        )
    ]
    for node in candidates:
        if node.name == name:
            return node
    return candidates[0] if candidates else None


def _signature(node: SymbolNode) -> str:
    if isinstance(node, ast.ClassDef):
        parts = [ast.unparse(base) for base in node.bases]
        parts.extend(
            f"{keyword.arg}={ast.unparse(keyword.value)}"
            if keyword.arg is not None
            else f"**{ast.unparse(keyword.value)}"
            for keyword in node.keywords
        )
        suffix = f"({', '.join(parts)})" if parts else ""
        return f"class {node.name}{suffix}:"

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}:"


def _parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[Parameter, ...]:
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(node.args.defaults)
    )
    defaults.extend(node.args.defaults)

    parameters = [
        Parameter(
            name=argument.arg,
            annotation=_annotation(argument.annotation),
            default=_annotation(default),
        )
        for argument, default in zip(positional, defaults)
    ]
    if node.args.vararg is not None:
        parameters.append(
            Parameter(
                name=f"*{node.args.vararg.arg}",
                annotation=_annotation(node.args.vararg.annotation),
            )
        )
    for argument, default in zip(
        node.args.kwonlyargs,
        node.args.kw_defaults,
    ):
        parameters.append(
            Parameter(
                name=argument.arg,
                annotation=_annotation(argument.annotation),
                default=_annotation(default),
            )
        )
    if node.args.kwarg is not None:
        parameters.append(
            Parameter(
                name=f"**{node.args.kwarg.arg}",
                annotation=_annotation(node.args.kwarg.annotation),
            )
        )
    return tuple(parameters)


def _raises(node: SymbolNode) -> tuple[str, ...]:
    names: list[str] = []

    def visit(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(
                child,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                ),
            ):
                continue
            if isinstance(child, ast.Raise) and child.exc is not None:
                name = ast.unparse(
                    child.exc.func
                    if isinstance(child.exc, ast.Call)
                    else child.exc
                )
                if name not in names:
                    names.append(name)
            visit(child)

    visit(node)
    return tuple(names)


def _python_placement(
    node: SymbolNode,
    target: CommentTarget,
    indent: str,
    *,
    replace: bool,
) -> Placement:
    if not node.body:
        raise CommentError("invalid_target", "The selected symbol has no body.")
    first = node.body[0]
    if first.lineno == node.lineno:
        raise CommentError(
            "unsupported_layout",
            "One-line function bodies are not supported.",
        )
    line = target.start_line + first.lineno - 1
    body_indent = indent + " " * first.col_offset
    if replace:
        end_line = target.start_line + (
            first.end_lineno or first.lineno
        )
        return Placement(
            line=line,
            indent=body_indent,
            end_line=end_line,
        )
    return Placement(line=line, indent=body_indent)


def _annotation(node: ast.expr | None) -> str | None:
    return ast.unparse(node) if node is not None else None


def _analyze_typescript(target: CommentTarget) -> TargetAnalysis:
    if not target.source_text.strip():
        raise CommentError("invalid_target", "The selected symbol is empty.")

    lines = target.source_text.splitlines()
    jsdoc = _leading_jsdoc(lines)
    declaration_index = _declaration_line(lines, jsdoc)
    if declaration_index is None:
        raise CommentError(
            "invalid_target",
            "Unable to find the selected declaration.",
        )

    declaration = "\n".join(lines[declaration_index:])
    header_end = _js_header_end(declaration)
    header = _collapse_whitespace(declaration[:header_end]).strip()
    if not header:
        raise CommentError(
            "invalid_target",
            "Unable to read the selected declaration.",
        )

    if target.symbol_kind == "class":
        parameters: tuple[Parameter, ...] = ()
        returns = None
    else:
        parameter_text = _js_parameter_text(declaration)
        parameters = (
            _parse_js_parameters(parameter_text)
            if parameter_text is not None
            else ()
        )
        returns = _js_return_type(declaration)

    existing_comment = (
        "\n".join(lines[jsdoc[0] : jsdoc[1] + 1])
        if jsdoc is not None
        else None
    )
    if jsdoc is not None:
        placement = Placement(
            line=target.start_line + jsdoc[0],
            indent=_leading_whitespace(lines[jsdoc[0]]),
            end_line=target.start_line + jsdoc[1] + 1,
        )
    else:
        first_line = next(
            (line for line in lines if line.strip()),
            lines[0],
        )
        placement = Placement(
            line=target.start_line,
            indent=_leading_whitespace(first_line),
        )

    return TargetAnalysis(
        signature=header,
        parameters=parameters,
        returns=returns,
        raises=(),
        decorators=tuple(
            line.strip()
            for line in lines[:declaration_index]
            if line.strip().startswith("@")
        ),
        class_context=target.class_context,
        existing_comment=existing_comment,
        placement=placement,
    )


def _leading_jsdoc(lines: list[str]) -> tuple[int, int] | None:
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not lines[index].strip().startswith("/**"):
        return None
    for end in range(index, len(lines)):
        if "*/" in lines[end]:
            return index, end
    return None


def _declaration_line(
    lines: list[str],
    jsdoc: tuple[int, int] | None = None,
) -> int | None:
    start = jsdoc[1] + 1 if jsdoc is not None else 0
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("@"):
            continue
        return index
    return None


def _leading_whitespace(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _js_header_end(declaration: str) -> int:
    paren = _find_top_level(declaration, "(")
    search_from = 0
    if paren is not None:
        close = _scan_balanced(declaration, paren, "(", ")")
        if close is not None:
            tail = declaration[close + 1 :]
            match = re.match(r"\s*:\s*", tail)
            if match is not None:
                return close + 1 + _js_type_end(tail, match.end())
            search_from = close + 1

    tail = declaration[search_from:]
    candidates: list[int] = []
    for needle in ("=>", "{"):
        index = _find_top_level(tail, needle)
        if index is not None:
            candidates.append(search_from + index)
    return min(candidates) if candidates else len(declaration)


def _js_parameter_text(declaration: str) -> str | None:
    open_index = _find_top_level(declaration, "(")
    if open_index is None:
        return None
    close = _scan_balanced(declaration, open_index, "(", ")")
    if close is None:
        return None
    return declaration[open_index + 1 : close]


def _js_return_type(declaration: str) -> str | None:
    open_index = _find_top_level(declaration, "(")
    if open_index is None:
        return None
    close = _scan_balanced(declaration, open_index, "(", ")")
    if close is None:
        return None

    tail = declaration[close + 1 :]
    match = re.match(r"\s*:\s*", tail)
    if match is None:
        return None
    start = match.end()
    end = _js_type_end(tail, start)
    value = _collapse_whitespace(tail[start:end]).strip()
    return value or None


def _js_type_end(text: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in "'\"`":
            index = _skip_string(text, index)
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] in "/*":
            index = _skip_comment(text, index)
            continue
        if depth == 0 and text.startswith("=>", index):
            return index
        if depth == 0 and char == "{":
            close = _scan_balanced(text, index, "{", "}")
            if close is None:
                return index
            after = text[close + 1 :].lstrip()
            if after.startswith(("=>", "{", "|", "&")):
                index = close + 1
                continue
            return index
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        index += 1
    return len(text)


def _parse_js_parameters(text: str) -> tuple[Parameter, ...]:
    parameters: list[Parameter] = []
    for part in _split_top_level(text, ","):
        part = part.strip()
        if not part:
            continue

        default = None
        equals = _find_top_level(part, "=")
        if equals is not None:
            default = part[equals + 1 :].strip()
            part = part[:equals].strip()

        annotation = None
        name = part
        colon = _find_top_level(part, ":")
        if colon is not None:
            name = part[:colon].strip()
            annotation = part[colon + 1 :].strip()

        name = name.lstrip(".").rstrip("?").strip()
        if not name or name == "this":
            continue

        parameters.append(
            Parameter(
                name=name,
                annotation=annotation or None,
                default=default,
            )
        )
    return tuple(parameters)


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in "'\"`":
            index = _skip_string(text, index)
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] in "/*":
            index = _skip_comment(text, index)
            continue
        if depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            index += len(separator)
            start = index
            continue
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        index += 1
    parts.append(text[start:])
    return parts


def _find_top_level(text: str, needle: str) -> int | None:
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in "'\"`":
            index = _skip_string(text, index)
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] in "/*":
            index = _skip_comment(text, index)
            continue
        if depth == 0 and text.startswith(needle, index):
            return index
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            if not (char == ">" and index > 0 and text[index - 1] == "="):
                depth = max(0, depth - 1)
        index += 1
    return None


def _scan_balanced(
    text: str,
    start: int,
    open_char: str,
    close_char: str,
) -> int | None:
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in "'\"`":
            index = _skip_string(text, index)
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] in "/*":
            index = _skip_comment(text, index)
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _skip_string(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return len(text)


def _skip_comment(text: str, start: int) -> int:
    if text.startswith("//", start):
        end = text.find("\n", start)
        return len(text) if end < 0 else end + 1
    end = text.find("*/", start + 2)
    return len(text) if end < 0 else end + 2


__all__ = [
    "CommentError",
    "CommentTarget",
    "Parameter",
    "Placement",
    "TargetAnalysis",
    "analyze_target",
]

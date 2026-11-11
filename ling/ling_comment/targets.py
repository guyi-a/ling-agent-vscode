"""Comment targets and language-aware target analysis."""

from __future__ import annotations

import ast
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
    # Zero-based line where the comment is inserted.
    line: int
    # Whitespace prefix applied to every inserted line.
    indent: str


@dataclass(slots=True, frozen=True)
class TargetAnalysis:
    signature: str
    parameters: tuple[Parameter, ...]
    returns: str | None
    raises: tuple[str, ...]
    decorators: tuple[str, ...]
    class_context: str | None
    existing_docstring: str | None
    placement: Placement


SymbolNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef

SUPPORTED_LANGUAGES = frozenset({"python"})


def analyze_target(target: CommentTarget) -> TargetAnalysis:
    """Extract structured context used by prompts and checks."""

    if target.language_id not in SUPPORTED_LANGUAGES:
        raise CommentError(
            "unsupported_language",
            "Comment generation currently supports Python only.",
        )
    return _analyze_python(target)


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
    if existing is not None:
        raise CommentError(
            "already_commented",
            "This symbol already has a docstring.",
        )

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
        existing_docstring=None,
        placement=_placement(node, target, indent),
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


def _placement(
    node: SymbolNode,
    target: CommentTarget,
    indent: str,
) -> Placement:
    if not node.body:
        raise CommentError("invalid_target", "The selected symbol has no body.")
    first = node.body[0]
    if first.lineno == node.lineno:
        raise CommentError(
            "unsupported_layout",
            "One-line function bodies are not supported.",
        )
    return Placement(
        line=target.start_line + first.lineno - 1,
        indent=indent + " " * first.col_offset,
    )


def _annotation(node: ast.expr | None) -> str | None:
    return ast.unparse(node) if node is not None else None


__all__ = [
    "CommentError",
    "CommentTarget",
    "Parameter",
    "Placement",
    "TargetAnalysis",
    "analyze_target",
]

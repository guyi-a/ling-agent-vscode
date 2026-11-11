"""One-shot comment generation pipeline (B0)."""

from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass

from ling_ai.stream import StreamFunction, StreamOptions
from ling_ai.types import Context, Model, TextContent, UserMessage

from .prompt import build_user_prompt, system_prompt
from .targets import (
    PYTHON_LANGUAGES,
    CommentError,
    CommentTarget,
    Placement,
    TargetAnalysis,
    analyze_target,
)


@dataclass(slots=True, frozen=True)
class CommentResult:
    comment: str
    placement: Placement
    analysis: TargetAnalysis


async def generate_comment(
    target: CommentTarget,
    *,
    model: Model,
    stream_fn: StreamFunction,
) -> CommentResult:
    analysis = analyze_target(target)
    context = Context(
        system_prompt=system_prompt(target.language_id),
        messages=[
            UserMessage(
                content=build_user_prompt(target, analysis),
                timestamp=int(time.time() * 1000),
            )
        ],
    )
    stream = stream_fn(
        model,
        context,
        StreamOptions(temperature=0.2, max_tokens=800),
    )
    message = await stream.result()
    if message.stop_reason == "error":
        raise CommentError(
            "model_error",
            message.error_message or "The model request failed.",
        )

    text = "".join(
        block.text
        for block in message.content
        if isinstance(block, TextContent)
    )
    return CommentResult(
        comment=normalize_comment(text, target.language_id),
        placement=analysis.placement,
        analysis=analysis,
    )


def normalize_comment(text: str, language_id: str) -> str:
    cleaned = _strip_fence(text)
    if not cleaned:
        raise CommentError(
            "empty_response",
            "The model returned an empty comment.",
        )
    if language_id in PYTHON_LANGUAGES:
        return _normalize_docstring(cleaned)
    return _normalize_jsdoc(cleaned)


def _strip_fence(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = re.search(
        r"```[A-Za-z0-9_-]*\s*\n(.*?)\n```",
        cleaned,
        re.DOTALL,
    )
    if fenced is not None:
        cleaned = fenced.group(1).strip()
    return cleaned


def _normalize_docstring(cleaned: str) -> str:
    if not (cleaned.startswith('"""') or cleaned.startswith("'''")):
        cleaned = f'"""{cleaned}"""'
    try:
        ast.parse(cleaned)
    except SyntaxError as exc:
        raise CommentError(
            "invalid_comment",
            f"The model returned an invalid docstring: {exc.msg}",
        ) from exc
    return cleaned


def _normalize_jsdoc(cleaned: str) -> str:
    if cleaned.startswith("/**") and cleaned.endswith("*/"):
        return cleaned
    if cleaned.startswith("/*") and cleaned.endswith("*/"):
        cleaned = cleaned[2:-2].strip()

    lines: list[str] = []
    for line in cleaned.splitlines():
        line = line.strip()
        if line.startswith("*"):
            line = line[1:].lstrip()
        lines.append(f" * {line}".rstrip())
    return "/**\n" + "\n".join(lines) + "\n */"


__all__ = ["CommentResult", "generate_comment", "normalize_comment"]

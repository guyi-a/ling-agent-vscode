"""One-shot comment generation pipeline (B0)."""

from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass

from ling_ai.stream import StreamFunction, StreamOptions
from ling_ai.types import Context, Model, TextContent, UserMessage

from .prompt import SYSTEM_PROMPT, build_user_prompt
from .targets import (
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
        system_prompt=SYSTEM_PROMPT,
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
        comment=normalize_docstring(text),
        placement=analysis.placement,
        analysis=analysis,
    )


def normalize_docstring(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = re.search(
        r"```[A-Za-z0-9_-]*\s*\n(.*?)\n```",
        cleaned,
        re.DOTALL,
    )
    if fenced is not None:
        cleaned = fenced.group(1).strip()
    if not cleaned:
        raise CommentError(
            "empty_response",
            "The model returned an empty comment.",
        )
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


__all__ = ["CommentResult", "generate_comment", "normalize_docstring"]

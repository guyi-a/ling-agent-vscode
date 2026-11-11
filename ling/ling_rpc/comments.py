"""RPC bridge for deterministic comment generation."""

from __future__ import annotations

from ling_ai.stream import StreamFunction
from ling_ai.types import Model
from ling_comment.pipeline import generate_comment
from ling_comment.targets import CommentTarget

from .provider import resolve_model


class CommentService:
    """Generate comments outside the agent loop."""

    def __init__(
        self,
        *,
        model: Model | None = None,
        stream_fn: StreamFunction | None = None,
    ) -> None:
        self._configured_model = model
        self._configured_stream = stream_fn

    async def generate(self, target: CommentTarget) -> dict[str, object]:
        model, stream_fn = resolve_model(
            self._configured_model,
            self._configured_stream,
        )
        result = await generate_comment(
            target,
            model=model,
            stream_fn=stream_fn,
        )
        return {
            "comment": result.comment,
            "placement": {
                "line": result.placement.line,
                "indent": result.placement.indent,
            },
        }


__all__ = ["CommentService"]

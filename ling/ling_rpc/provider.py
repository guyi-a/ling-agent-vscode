"""Resolve the configured provider model and stream function."""

from __future__ import annotations

import os

from ling_ai.adapters.openai_completions import (
    create_openai_completions_stream,
)
from ling_ai.stream import StreamFunction
from ling_ai.types import Model


def resolve_model(
    model: Model | None,
    stream_fn: StreamFunction | None,
) -> tuple[Model, StreamFunction]:
    if stream_fn is not None:
        return (
            model
            or Model(
                id="test-model",
                provider="test",
                api="openai-completions",
            ),
            stream_fn,
        )

    api_key = (
        os.environ.get("LING_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "Missing API key. Set LING_API_KEY or DEEPSEEK_API_KEY."
        )

    resolved = model or Model(
        id=os.environ.get("LING_MODEL", "deepseek-chat"),
        provider=os.environ.get("LING_PROVIDER", "deepseek"),
        api="openai-completions",
        base_url=os.environ.get(
            "LING_API_BASE_URL",
            "https://api.deepseek.com/v1",
        ),
    )
    return resolved, create_openai_completions_stream(api_key=api_key)


__all__ = ["resolve_model"]

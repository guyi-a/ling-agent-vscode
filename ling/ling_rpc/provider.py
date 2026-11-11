"""Resolve the configured provider model and stream function."""

from __future__ import annotations

import os

from ling_ai.adapters.openai_completions import (
    create_openai_completions_stream,
)
from ling_ai.stream import StreamFunction
from ling_ai.types import Model
from ling_retrieval.embedding import OpenAIEmbedder


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


def resolve_embedder() -> OpenAIEmbedder | None:
    """Build an embedder when semantic search is configured."""
    model = os.environ.get("LING_EMBEDDING_MODEL", "").strip()
    if not model:
        return None

    base_url = (
        os.environ.get("LING_EMBEDDING_BASE_URL", "").strip()
        or os.environ.get("LING_API_BASE_URL", "").strip()
    )
    if not base_url:
        return None

    api_key = os.environ.get("LING_EMBEDDING_API_KEY", "").strip()
    if not api_key:
        # Reuse the chat key only for the same provider; otherwise a
        # DeepSeek key would be sent to an embeddings endpoint that
        # cannot accept it.
        chat_base = os.environ.get("LING_API_BASE_URL", "").strip()
        if _same_endpoint(base_url, chat_base):
            api_key = (
                os.environ.get("LING_API_KEY", "").strip()
                or os.environ.get("DEEPSEEK_API_KEY", "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
            )
    if not api_key:
        return None

    return OpenAIEmbedder(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def _same_endpoint(left: str, right: str) -> bool:
    return bool(left) and bool(right) and (
        left.rstrip("/").lower() == right.rstrip("/").lower()
    )


__all__ = ["resolve_embedder", "resolve_model"]

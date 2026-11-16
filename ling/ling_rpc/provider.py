"""Resolve the configured provider model and stream function."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from urllib.parse import urlsplit

from ling_agent.web import (
    DEFAULT_WEB_BASE_URL,
    DEFAULT_WEB_TIMEOUT_SECONDS,
    WebClient,
)
from ling_ai.adapters.anthropic_messages import (
    create_anthropic_messages_stream,
)
from ling_ai.adapters.openai_completions import (
    create_openai_completions_stream,
)
from ling_ai.stream import StreamFunction
from ling_ai.types import Model
from ling_retrieval.embedding import OpenAIEmbedder

OPENAI_COMPLETIONS = "openai-completions"
ANTHROPIC_MESSAGES = "anthropic-messages"
_SUPPORTED_PROTOCOLS = (OPENAI_COMPLETIONS, ANTHROPIC_MESSAGES)

_OPENAI_DEFAULT_MODEL = "deepseek-flash"
_OPENAI_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
_ANTHROPIC_DEFAULT_MODEL = "deepseek-flash"
_ANTHROPIC_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"

_logged_anthropic = False


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
                api=OPENAI_COMPLETIONS,
            ),
            stream_fn,
        )

    protocol = _api_protocol(model)
    api_key = _chat_api_key(protocol)
    if not api_key:
        if protocol == ANTHROPIC_MESSAGES:
            raise RuntimeError(
                "Missing API key. Set LING_API_KEY, ANTHROPIC_API_KEY, "
                "or DEEPSEEK_API_KEY."
            )
        raise RuntimeError(
            "Missing API key. Set LING_API_KEY or DEEPSEEK_API_KEY."
        )

    if model is not None:
        model = replace(model, api=protocol)

    resolved = model or Model(
        id=_env_value(
            "LING_MODEL",
            _default_model(protocol),
        ),
        provider=_env_value("LING_PROVIDER", "deepseek"),
        api=protocol,
        base_url=_env_value(
            "LING_API_BASE_URL",
            _default_base_url(protocol),
        ),
    )

    if protocol == ANTHROPIC_MESSAGES:
        _log_anthropic_protocol()
        return resolved, create_anthropic_messages_stream(api_key=api_key)
    return resolved, create_openai_completions_stream(api_key=api_key)


def resolve_embedder() -> OpenAIEmbedder | None:
    """Build an embedder when semantic search is configured."""
    protocol = _api_protocol()
    model = _env_value("LING_EMBEDDING_MODEL", "")
    if not model:
        return None

    chat_base = _env_value("LING_API_BASE_URL", "")
    configured_base = _env_value("LING_EMBEDDING_BASE_URL", "")
    base_url = (
        configured_base
        if protocol == ANTHROPIC_MESSAGES
        else configured_base or chat_base
    )
    if not base_url:
        return None

    api_key = _env_value("LING_EMBEDDING_API_KEY", "")
    if not api_key and protocol != ANTHROPIC_MESSAGES:
        # Reuse the chat key only for the same OpenAI-compatible endpoint;
        # an Anthropic key must never be sent to an embeddings endpoint.
        if _same_endpoint(base_url, chat_base):
            api_key = _chat_api_key(OPENAI_COMPLETIONS)
    if not api_key:
        return None

    return OpenAIEmbedder(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def resolve_web_client(model: Model) -> WebClient | None:
    """Build the DeepSeek web client without affecting other providers."""
    protocol = _api_protocol(model)
    api_key = _chat_api_key(protocol)
    if not api_key:
        return None

    configured = _env_value("LING_WEB_BASE_URL", "")
    base_url = configured or model.base_url or ""
    if not configured:
        if not _is_deepseek_endpoint(base_url):
            return None
        if protocol == ANTHROPIC_MESSAGES:
            base_url = _deepseek_web_root(base_url)

    timeout = _positive_float_env(
        "LING_WEB_TIMEOUT_SECONDS",
        DEFAULT_WEB_TIMEOUT_SECONDS,
    )
    return WebClient(
        api_key=api_key,
        base_url=base_url or DEFAULT_WEB_BASE_URL,
        timeout_seconds=timeout,
    )


def _api_protocol(model: Model | None = None) -> str:
    value = (
        model.api
        if model is not None and model.api
        else os.environ.get("LING_API_PROTOCOL", "")
    ).strip()
    value = value or OPENAI_COMPLETIONS
    if value not in _SUPPORTED_PROTOCOLS:
        raise RuntimeError(
            f"Unsupported LING_API_PROTOCOL: {value!r}. "
            f"Use {' or '.join(_SUPPORTED_PROTOCOLS)}."
        )
    return value


def _chat_api_key(protocol: str) -> str:
    if protocol == ANTHROPIC_MESSAGES:
        return (
            _env_value("LING_API_KEY", "")
            or _env_value("ANTHROPIC_API_KEY", "")
            or _env_value("DEEPSEEK_API_KEY", "")
        )
    return (
        _env_value("LING_API_KEY", "")
        or _env_value("DEEPSEEK_API_KEY", "")
        or _env_value("OPENAI_API_KEY", "")
    )


def _default_model(protocol: str) -> str:
    if protocol == ANTHROPIC_MESSAGES:
        return _ANTHROPIC_DEFAULT_MODEL
    return _OPENAI_DEFAULT_MODEL


def _default_base_url(protocol: str) -> str:
    if protocol == ANTHROPIC_MESSAGES:
        return _ANTHROPIC_DEFAULT_BASE_URL
    return _OPENAI_DEFAULT_BASE_URL


def _env_value(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _log_anthropic_protocol() -> None:
    global _logged_anthropic
    if _logged_anthropic:
        return
    _logged_anthropic = True
    print("Ling API protocol: anthropic-messages", file=sys.stderr)


def _same_endpoint(left: str, right: str) -> bool:
    return bool(left) and bool(right) and (
        left.rstrip("/").lower() == right.rstrip("/").lower()
    )


def _is_deepseek_endpoint(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return host == "deepseek.com" or host.endswith(".deepseek.com")


def _deepseek_web_root(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return DEFAULT_WEB_BASE_URL
    if not parts.scheme or not parts.netloc:
        return DEFAULT_WEB_BASE_URL
    return f"{parts.scheme}://{parts.netloc}"


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


__all__ = ["resolve_embedder", "resolve_model", "resolve_web_client"]

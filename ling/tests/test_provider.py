"""Tests for provider protocol selection and endpoint guards."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from ling_ai.types import Model
from ling_rpc.provider import (
    ANTHROPIC_MESSAGES,
    OPENAI_COMPLETIONS,
    OPENAI_RESPONSES,
    resolve_embedder,
    resolve_model,
    resolve_web_client,
)

_ENV_KEYS = (
    "LING_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LING_MODEL",
    "LING_API_BASE_URL",
    "LING_API_PROTOCOL",
    "LING_PROVIDER",
    "LING_EMBEDDING_MODEL",
    "LING_EMBEDDING_BASE_URL",
    "LING_EMBEDDING_API_KEY",
    "LING_WEB_BASE_URL",
    "LING_WEB_TIMEOUT_SECONDS",
)


@contextmanager
def _environment(**values: str) -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _stream_source(stream) -> str:
    return stream.__code__.co_filename.replace("\\", "/")


def test_default_protocol_is_openai() -> None:
    with _environment(LING_API_KEY="key"):
        model, stream = resolve_model(None, None)

    assert model.id == "deepseek-flash"
    assert model.api == OPENAI_COMPLETIONS
    assert model.base_url == "https://api.deepseek.com/v1"
    assert "openai_completions.py" in _stream_source(stream)


def test_anthropic_protocol_defaults() -> None:
    with _environment(
        LING_API_KEY="key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
    ):
        model, stream = resolve_model(None, None)

    assert model.id == "deepseek-flash"
    assert model.api == ANTHROPIC_MESSAGES
    assert model.base_url == "https://api.deepseek.com/anthropic"
    assert "anthropic_messages.py" in _stream_source(stream)


def test_openai_responses_protocol_defaults() -> None:
    with _environment(
        LING_API_KEY="key",
        LING_API_PROTOCOL=OPENAI_RESPONSES,
        LING_MODEL="gpt-6.1-sol",
        LING_API_BASE_URL="http://192.168.18.80:14000/v1",
    ):
        model, stream = resolve_model(None, None)

    assert model.id == "gpt-6.1-sol"
    assert model.api == OPENAI_RESPONSES
    assert model.base_url == "http://192.168.18.80:14000/v1"
    assert "openai_responses.py" in _stream_source(stream)


def test_explicit_model_and_env_overrides() -> None:
    with _environment(
        LING_API_KEY="key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
        LING_MODEL="custom-model",
        LING_API_BASE_URL="https://example.test/anthropic",
    ):
        model, _ = resolve_model(None, None)

    assert model.id == "custom-model"
    assert model.base_url == "https://example.test/anthropic"

    explicit = Model(
        id="explicit",
        provider="test",
        api=ANTHROPIC_MESSAGES,
        base_url="https://example.test/explicit",
    )
    with _environment(LING_API_KEY="key"):
        model, stream = resolve_model(explicit, None)

    assert model.id == "explicit"
    assert model.api == ANTHROPIC_MESSAGES
    assert "anthropic_messages.py" in _stream_source(stream)


def test_explicit_model_without_api_uses_env_protocol() -> None:
    explicit = Model(id="explicit", provider="test", api="")
    with _environment(
        LING_API_KEY="key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
    ):
        model, stream = resolve_model(explicit, None)

    assert model.api == ANTHROPIC_MESSAGES
    assert "anthropic_messages.py" in _stream_source(stream)


def test_invalid_protocol_raises() -> None:
    with _environment(
        LING_API_KEY="key",
        LING_API_PROTOCOL="grpc",
    ):
        try:
            resolve_model(None, None)
        except RuntimeError as exc:
            assert "grpc" in str(exc)
        else:
            raise AssertionError("invalid protocol should raise")


def test_anthropic_key_fallback() -> None:
    with _environment(
        ANTHROPIC_API_KEY="anthropic-key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
    ):
        model, _ = resolve_model(None, None)

    assert model.api == ANTHROPIC_MESSAGES

    with _environment(
        DEEPSEEK_API_KEY="deepseek-key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
    ):
        model, _ = resolve_model(None, None)

    assert model.api == ANTHROPIC_MESSAGES


def test_missing_anthropic_key_names_the_right_variable() -> None:
    with _environment(LING_API_PROTOCOL=ANTHROPIC_MESSAGES):
        try:
            resolve_model(None, None)
        except RuntimeError as exc:
            assert "ANTHROPIC_API_KEY" in str(exc)
        else:
            raise AssertionError("missing key should raise")


def test_embedding_does_not_reuse_anthropic_chat_credentials() -> None:
    with _environment(
        LING_API_KEY="chat-key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
        LING_API_BASE_URL="https://api.deepseek.com/anthropic",
        LING_EMBEDDING_MODEL="text-embedding-v4",
    ):
        assert resolve_embedder() is None

    with _environment(
        LING_API_KEY="chat-key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
        LING_API_BASE_URL="https://api.deepseek.com/anthropic",
        LING_EMBEDDING_MODEL="text-embedding-v4",
        LING_EMBEDDING_BASE_URL="https://dashscope.example/v1",
    ):
        assert resolve_embedder() is None

    with _environment(
        LING_API_KEY="chat-key",
        LING_API_PROTOCOL=ANTHROPIC_MESSAGES,
        LING_EMBEDDING_MODEL="text-embedding-v4",
        LING_EMBEDDING_BASE_URL="https://dashscope.example/v1",
        LING_EMBEDDING_API_KEY="embedding-key",
    ):
        assert resolve_embedder() is not None


def test_embedding_reuses_openai_chat_credentials() -> None:
    with _environment(
        LING_API_KEY="chat-key",
        LING_API_BASE_URL="https://api.deepseek.com/v1",
        LING_EMBEDDING_MODEL="text-embedding-v4",
    ):
        embedder = resolve_embedder()

    assert embedder is not None
    assert embedder.model == "text-embedding-v4"


def test_web_client_normalizes_deepseek_anthropic_base() -> None:
    model = Model(
        id="deepseek-flash",
        provider="deepseek",
        api=ANTHROPIC_MESSAGES,
        base_url="https://api.deepseek.com/anthropic",
    )
    with _environment(LING_API_KEY="key"):
        client = resolve_web_client(model)

    assert client is not None
    assert client.base_url == "https://api.deepseek.com"

    native = Model(
        id="claude",
        provider="anthropic",
        api=ANTHROPIC_MESSAGES,
        base_url="https://api.anthropic.com",
    )
    with _environment(LING_API_KEY="key"):
        assert resolve_web_client(native) is None


if __name__ == "__main__":
    test_default_protocol_is_openai()
    test_anthropic_protocol_defaults()
    test_openai_responses_protocol_defaults()
    test_explicit_model_and_env_overrides()
    test_explicit_model_without_api_uses_env_protocol()
    test_invalid_protocol_raises()
    test_anthropic_key_fallback()
    test_missing_anthropic_key_names_the_right_variable()
    test_embedding_does_not_reuse_anthropic_chat_credentials()
    test_embedding_reuses_openai_chat_credentials()
    test_web_client_normalizes_deepseek_anthropic_base()
    print("all tests passed")

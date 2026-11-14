"""OpenAI-compatible embedding client for code retrieval."""

from __future__ import annotations

import time
from typing import Protocol, Sequence

import httpx

DEFAULT_BATCH_SIZE = 10
DEFAULT_TIMEOUT_SECONDS = 20.0
COOLDOWN_SECONDS = 60.0
MAX_EMBED_CHARS = 4_000


class EmbeddingError(RuntimeError):
    """Raised when an embedding provider cannot return vectors."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


def embedding_text(
    *,
    kind: str,
    symbol: str,
    signature: str,
    comment: str,
    body: str,
) -> str:
    """Build the text sent to the embedding model for one chunk."""
    parts = [
        f"{kind} {symbol}".strip(),
        signature.strip(),
        comment.strip(),
        body.strip(),
    ]
    return "\n".join(part for part in parts if part)[:MAX_EMBED_CHARS]


class OpenAIEmbedder:
    """Call any OpenAI-compatible /embeddings endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        dimensions: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = _endpoint(base_url)
        self._batch_size = max(1, batch_size)
        self._dimensions = dimensions
        # Embedding requests connect directly; system proxy settings add latency.
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)
        self._cooldown_until = 0.0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if time.monotonic() < self._cooldown_until:
            raise EmbeddingError(
                "Embedding provider is cooling down after a failure"
            )

        results: list[list[float]] = []
        index = 0
        batch_size = self._batch_size
        while index < len(texts):
            batch = list(texts[index : index + batch_size])
            try:
                vectors = self._request(batch)
            except EmbeddingError as error:
                if error.status == 400 and len(batch) > 1:
                    batch_size = max(1, len(batch) // 2)
                    self._batch_size = batch_size
                    continue
                self._cooldown_until = time.monotonic() + COOLDOWN_SECONDS
                raise
            results.extend(vectors)
            index += len(batch)
        return results

    def close(self) -> None:
        self._client.close()

    def _request(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self._dimensions is not None:
            payload["dimensions"] = self._dimensions

        for attempt in (0, 1):
            try:
                response = self._client.post(
                    self._base_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                    },
                    json=payload,
                )
            except httpx.HTTPError as error:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise EmbeddingError(
                    f"Embedding request failed: {error}"
                ) from error

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise EmbeddingError(
                    "Embedding request failed with HTTP "
                    f"{response.status_code}",
                    status=response.status_code,
                )
            if response.status_code >= 400:
                raise EmbeddingError(
                    "Embedding request failed with HTTP "
                    f"{response.status_code}: {response.text[:300]}",
                    status=response.status_code,
                )
            return _parse_embeddings(response, len(texts))

        raise EmbeddingError("Embedding request failed")


def _endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/embeddings"):
        return normalized
    return f"{normalized}/embeddings"


def _parse_embeddings(
    response: httpx.Response,
    expected: int,
) -> list[list[float]]:
    try:
        data = response.json()
    except ValueError as error:
        raise EmbeddingError(
            "Embedding response was not JSON"
        ) from error

    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) != expected:
        raise EmbeddingError(
            "Embedding response had "
            f"{len(rows) if isinstance(rows, list) else 0} vectors "
            f"for {expected} inputs"
        )

    ordered = sorted(
        rows,
        key=lambda row: row.get("index", 0)
        if isinstance(row, dict)
        else 0,
    )
    vectors: list[list[float]] = []
    for row in ordered:
        vector = row.get("embedding") if isinstance(row, dict) else None
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(
                "Embedding response contained an invalid vector"
            )
        vectors.append([float(value) for value in vector])
    return vectors


__all__ = [
    "COOLDOWN_SECONDS",
    "DEFAULT_BATCH_SIZE",
    "Embedder",
    "EmbeddingError",
    "OpenAIEmbedder",
    "embedding_text",
]

"""Vector packing, cosine similarity, and rank fusion."""

from __future__ import annotations

import array
import math
from operator import mul
from typing import Iterable, Sequence

try:  # numpy is optional; the pure-Python path is slower but portable.
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only without numpy
    _np = None

RRF_K = 60


def normalize(values: Sequence[float]) -> list[float]:
    """Scale a vector to unit length; zero vectors stay zero."""
    norm = math.sqrt(math.fsum(value * value for value in values))
    if norm == 0.0:
        return [0.0] * len(values)
    return [value / norm for value in values]


def pack_vector(values: Sequence[float]) -> bytes:
    """Normalize and pack a vector as float32 bytes."""
    return array.array("f", normalize(values)).tobytes()


def unpack_vector(blob: bytes) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    return values


def vector_dimensions(blob: bytes) -> int:
    return len(blob) // 4


def cosine_similarities(
    query: Sequence[float],
    blobs: Sequence[bytes],
) -> list[float]:
    """Cosine similarity between a query vector and packed unit vectors."""
    if not blobs:
        return []
    unit = normalize(query)
    if _np is not None:
        return _numpy_similarities(unit, blobs)
    return _python_similarities(unit, blobs)


def _numpy_similarities(
    unit: list[float],
    blobs: Sequence[bytes],
) -> list[float]:
    dimensions = len(unit)
    rows: list = []
    positions: list[int] = []
    for index, blob in enumerate(blobs):
        if len(blob) != dimensions * 4:
            continue
        rows.append(_np.frombuffer(blob, dtype=_np.float32))
        positions.append(index)

    scores = [-1.0] * len(blobs)
    if not rows:
        return scores
    matrix = _np.vstack(rows)
    values = matrix @ _np.asarray(unit, dtype=_np.float32)
    for position, value in zip(positions, values.tolist()):
        scores[position] = float(value)
    return scores


def _python_similarities(
    unit: list[float],
    blobs: Sequence[bytes],
) -> list[float]:
    dimensions = len(unit)
    scores: list[float] = []
    for blob in blobs:
        if len(blob) != dimensions * 4:
            scores.append(-1.0)
            continue
        values = unpack_vector(blob)
        scores.append(math.fsum(map(mul, unit, values)))
    return scores


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[int]],
    *,
    k: int = RRF_K,
    limit: int = 10,
) -> list[tuple[int, float]]:
    """Fuse ranked id lists with reciprocal rank fusion."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return ordered[:limit]


__all__ = [
    "RRF_K",
    "cosine_similarities",
    "normalize",
    "pack_vector",
    "reciprocal_rank_fusion",
    "unpack_vector",
    "vector_dimensions",
]

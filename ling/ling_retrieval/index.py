"""Persistent incremental code index for the sidecar."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

from .bm25 import (
    Bm25Index,
    SearchHit,
    split_identifier_text,
    tokenize_query,
)
from .chunking import SUFFIX_LANGUAGES, chunk_file, iter_source_files
from .embedding import Embedder, EmbeddingError, embedding_text
from .vectors import pack_vector, reciprocal_rank_fusion

INDEX_DIRECTORY = ".ling/code-index"
INDEX_FILENAME = "index.sqlite"
DEFAULT_TOP_K = 8
MAX_TOP_K = 20
PREVIEW_LINES = 24
PREVIEW_CHARS = 3_000
TEST_PATH_PENALTY = 8.0
TEST_SIMILARITY_PENALTY = 0.1
CANDIDATE_MIN = 20
CANDIDATE_MULTIPLIER = 4
EMBED_BUDGET_PER_SEARCH = 2_000
EMBED_BUDGET_BUILD = 10_000
EMBED_SLICE_CHUNKS = 100


class CodeIndex:
    """Workspace-scoped BM25 index with incremental file updates."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._db_path = (
            self._root / INDEX_DIRECTORY / INDEX_FILENAME
        )
        self._lock = threading.Lock()
        # Set by search(); each tool call uses its own CodeIndex.
        self.last_search_mode = "bm25"

    def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        language: str | None = None,
        path_prefix: str | None = None,
        embedder: Embedder | None = None,
    ) -> list[dict]:
        terms = tokenize_query(query)
        if not terms and embedder is None:
            return []
        limit = max(1, min(top_k, MAX_TOP_K))
        with self._lock:
            index = self._open()
            try:
                self._sync(index)
                candidates = max(
                    CANDIDATE_MIN,
                    limit * CANDIDATE_MULTIPLIER,
                )
                lexical = (
                    index.search(
                        terms,
                        candidates,
                        language=language,
                        path_prefix=path_prefix,
                        test_penalty=TEST_PATH_PENALTY,
                    )
                    if terms
                    else []
                )
                vector_hits = self._vector_hits(
                    index,
                    query,
                    embedder,
                    candidates,
                    language,
                    path_prefix,
                )
                if vector_hits:
                    hits = self._fuse(lexical, vector_hits, limit)
                    self.last_search_mode = "hybrid"
                else:
                    hits = lexical[:limit]
                    self.last_search_mode = "bm25"
                return self._format_results(hits, terms)
            finally:
                index.close()

    @staticmethod
    def _fuse(
        lexical: list[SearchHit],
        vector_hits: list[SearchHit],
        limit: int,
    ) -> list[SearchHit]:
        fused = reciprocal_rank_fusion(
            [
                [hit.rowid for hit in lexical],
                [hit.rowid for hit in vector_hits],
            ],
            limit=limit,
        )
        by_id = {hit.rowid: hit for hit in lexical}
        by_id.update(
            {hit.rowid: hit for hit in vector_hits}
        )
        return [
            by_id[rowid]
            for rowid, _ in fused
            if rowid in by_id
        ]

    def _vector_hits(
        self,
        index: Bm25Index,
        query: str,
        embedder: Embedder | None,
        limit: int,
        language: str | None,
        path_prefix: str | None,
    ) -> list[SearchHit]:
        if embedder is None:
            return []
        if not self._ensure_vectors(index, embedder):
            return []
        try:
            vector = embedder.embed([query])[0]
        except (EmbeddingError, IndexError):
            return []
        return index.search_vectors(
            vector,
            limit,
            model=embedder.model,
            language=language,
            path_prefix=path_prefix,
            test_penalty=TEST_SIMILARITY_PENALTY,
        )

    def status(self, *, embedder: Embedder | None = None) -> dict:
        """Report how much of the workspace is already indexed."""
        model = embedder.model if embedder is not None else None
        total_files = len(iter_source_files(self._root))
        if not self._db_path.exists():
            return {
                "files": 0,
                "totalFiles": total_files,
                "chunks": 0,
                "vectors": 0,
                "indexedFiles": 0,
                "percent": 0,
                "model": model,
            }
        with self._lock:
            index = self._open()
            try:
                return self._status(index, model, total_files)
            finally:
                index.close()

    def build(
        self,
        *,
        embedder: Embedder | None = None,
        limit: int = EMBED_BUDGET_BUILD,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        """Build or refresh the index and return counts."""
        with self._lock:
            index = self._open()
            try:
                self._sync(index)
                model = embedder.model if embedder is not None else None
                total_files = index.count_files()
                embedded = 0
                error: str | None = None
                if embedder is not None:
                    embedded, error = self._fill_vectors(
                        index,
                        embedder,
                        limit,
                        progress=progress,
                        total_files=total_files,
                    )
                stats = self._status(index, model, total_files)
                stats["embedded"] = embedded
                if error is not None:
                    stats["error"] = error
                if progress is not None:
                    progress(stats)
                return stats
            finally:
                index.close()

    @staticmethod
    def _status(
        index: Bm25Index,
        model: str | None,
        total_files: int,
    ) -> dict:
        files = index.count_files()
        if model is None:
            indexed_files = files
            vectors = 0
        else:
            indexed_files = index.indexed_files(model)
            vectors = index.count_vectors(model)
        denominator = files if model is not None else total_files
        percent = (
            min(100, round(indexed_files * 100 / denominator))
            if denominator
            else 0
        )
        return {
            "files": files,
            "totalFiles": total_files,
            "chunks": index.count(),
            "vectors": vectors,
            "indexedFiles": indexed_files,
            "percent": percent,
            "model": model,
        }

    def _ensure_vectors(
        self,
        index: Bm25Index,
        embedder: Embedder,
    ) -> bool:
        self._fill_vectors(
            index,
            embedder,
            EMBED_BUDGET_PER_SEARCH,
        )
        return index.count_vectors(embedder.model) > 0

    def _fill_vectors(
        self,
        index: Bm25Index,
        embedder: Embedder,
        limit: int,
        *,
        progress: Callable[[dict], None] | None = None,
        total_files: int | None = None,
    ) -> tuple[int, str | None]:
        current = index.vector_model()
        if current is not None and current != embedder.model:
            index.drop_vectors()
        pending = index.chunks_without_vectors(
            embedder.model,
            limit=limit,
        )
        if not pending:
            return 0, None
        if total_files is None:
            total_files = index.count_files()

        done = 0
        while done < len(pending):
            batch = pending[done : done + EMBED_SLICE_CHUNKS]
            texts = [
                embedding_text(
                    kind=row[3],
                    symbol=row[2],
                    signature=row[4],
                    comment=row[5],
                    body=row[6],
                )
                for row in batch
            ]
            try:
                vectors = embedder.embed(texts)
            except EmbeddingError as error:
                return done, str(error)
            if len(vectors) != len(batch):
                return done, (
                    "embedding provider returned "
                    f"{len(vectors)} vectors for {len(batch)} chunks"
                )
            index.save_vectors(
                embedder.model,
                [
                    (row[0], pack_vector(vector))
                    for row, vector in zip(batch, vectors)
                ],
            )
            done += len(batch)
            if progress is not None:
                progress(
                    self._status(
                        index,
                        embedder.model,
                        total_files,
                    )
                )
        return done, None

    def _open(self) -> Bm25Index:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return Bm25Index(self._db_path)
        except sqlite3.DatabaseError:
            self._db_path.unlink(missing_ok=True)
            return Bm25Index(self._db_path)

    def _sync(self, index: Bm25Index) -> None:
        states = index.file_states()
        current: set[str] = set()
        for path in iter_source_files(self._root):
            try:
                relative = path.relative_to(self._root).as_posix()
                stat = path.stat()
            except (OSError, ValueError):
                continue
            current.add(relative)
            previous = states.get(relative)
            if previous == (stat.st_mtime_ns, stat.st_size):
                continue
            chunks = chunk_file(path, self._root)
            index.upsert_file(
                relative,
                stat.st_mtime_ns,
                stat.st_size,
                SUFFIX_LANGUAGES.get(path.suffix, ""),
                chunks,
            )
        for stale in set(states) - current:
            index.delete_file(stale)

    def _format_results(
        self,
        hits: list[SearchHit],
        terms: list[str],
    ) -> list[dict]:
        results: list[dict] = []
        cache: dict[str, list[str]] = {}
        for hit in hits:
            lines = cache.get(hit.path)
            if lines is None:
                lines = self._read_lines(hit.path)
                cache[hit.path] = lines
            preview = "\n".join(
                lines[
                    hit.start_line - 1 : min(
                        hit.end_line,
                        hit.start_line - 1 + PREVIEW_LINES,
                    )
                ]
            )[:PREVIEW_CHARS]
            results.append(
                {
                    "path": hit.path,
                    "symbol": hit.symbol,
                    "kind": hit.kind,
                    "language": hit.language,
                    "startLine": hit.start_line,
                    "endLine": hit.end_line,
                    "signature": hit.signature,
                    "comment": hit.comment,
                    "score": round(hit.score, 4),
                    "matchedTerms": self._matched_terms(
                        hit,
                        preview,
                        terms,
                    ),
                    "preview": preview,
                }
            )
        return results

    def _read_lines(self, relative: str) -> list[str]:
        try:
            return (
                self._root / relative
            ).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

    @staticmethod
    def _matched_terms(
        hit: SearchHit,
        preview: str,
        terms: list[str],
    ) -> list[str]:
        tokens = set(
            split_identifier_text(
                " ".join(
                    [
                        hit.symbol,
                        hit.signature,
                        hit.comment,
                        preview,
                    ]
                )
            ).split()
        )
        return [term for term in terms if term in tokens]


__all__ = [
    "CodeIndex",
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
]

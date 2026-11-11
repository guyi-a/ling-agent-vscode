"""SQLite FTS5 BM25 index for code retrieval."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .chunking import Chunk
from .vectors import cosine_similarities

SCHEMA_VERSION = 2

_CAMEL_LOWER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_UPPER = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)


def split_identifier_text(text: str) -> str:
    text = _CAMEL_LOWER.sub(" ", text)
    text = _CAMEL_UPPER.sub(" ", text)
    return _NON_WORD.sub(" ", text).lower()


def tokenize_query(query: str) -> list[str]:
    tokens = [
        token
        for token in split_identifier_text(query).split()
        if len(token) > 1 and token not in STOPWORDS
    ]
    return list(dict.fromkeys(tokens))


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    score: float
    language: str = ""
    signature: str = ""
    comment: str = ""
    rowid: int = 0


class Bm25Index:
    """A small BM25 index backed by SQLite FTS5."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            self._db.executescript(
                """
                DROP TABLE IF EXISTS chunk_fts;
                DROP TABLE IF EXISTS chunk_vectors;
                DROP TABLE IF EXISTS chunk_meta;
                DROP TABLE IF EXISTS files;
                """
            )
            self._db.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION}"
            )
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                language TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunk_meta (
                rowid INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                language TEXT NOT NULL,
                symbol TEXT NOT NULL,
                kind TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                signature TEXT NOT NULL,
                comment TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                body_text TEXT NOT NULL DEFAULT ''
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                symbol_text,
                signature_text,
                comment_text,
                body_text,
                tokenize='unicode61'
            );
            CREATE TABLE IF NOT EXISTS chunk_vectors (
                rowid INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL
            );
            """
        )
        self._db.commit()

    def add(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            self._insert_chunk(chunk)
        self._db.commit()

    def upsert_file(
        self,
        path: str,
        mtime_ns: int,
        size: int,
        language: str,
        chunks: list[Chunk],
    ) -> None:
        # Reuse vectors for chunks whose body did not change.
        previous = self._vectors_by_hash(path)
        self.delete_file(path, commit=False)
        for chunk in chunks:
            rowid = self._insert_chunk(chunk)
            saved = previous.get(chunk.content_hash)
            if saved is not None:
                self._db.execute(
                    """
                    INSERT OR REPLACE INTO chunk_vectors(
                        rowid, model, dim, vector
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (rowid, *saved),
                )
        self._db.execute(
            """
            INSERT OR REPLACE INTO files(
                path, mtime_ns, size, language
            ) VALUES (?, ?, ?, ?)
            """,
            (path, mtime_ns, size, language),
        )
        self._db.commit()

    def delete_file(self, path: str, *, commit: bool = True) -> None:
        rowids = [
            row[0]
            for row in self._db.execute(
                "SELECT rowid FROM chunk_meta WHERE path = ?",
                (path,),
            )
        ]
        self._db.executemany(
            "DELETE FROM chunk_fts WHERE rowid = ?",
            [(rowid,) for rowid in rowids],
        )
        self._db.executemany(
            "DELETE FROM chunk_vectors WHERE rowid = ?",
            [(rowid,) for rowid in rowids],
        )
        self._db.execute(
            "DELETE FROM chunk_meta WHERE path = ?",
            (path,),
        )
        self._db.execute("DELETE FROM files WHERE path = ?", (path,))
        if commit:
            self._db.commit()

    def _vectors_by_hash(
        self,
        path: str,
    ) -> dict[str, tuple[str, int, bytes]]:
        return {
            row[0]: (row[1], row[2], row[3])
            for row in self._db.execute(
                """
                SELECT m.content_hash, v.model, v.dim, v.vector
                FROM chunk_meta AS m
                JOIN chunk_vectors AS v ON v.rowid = m.rowid
                WHERE m.path = ?
                """,
                (path,),
            )
        }

    def file_states(self) -> dict[str, tuple[int, int]]:
        return {
            row[0]: (row[1], row[2])
            for row in self._db.execute(
                "SELECT path, mtime_ns, size FROM files"
            )
        }

    def vector_model(self) -> str | None:
        row = self._db.execute(
            "SELECT model FROM chunk_vectors LIMIT 1"
        ).fetchone()
        return row[0] if row is not None else None

    def count_vectors(self, model: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM chunk_vectors WHERE model = ?",
            (model,),
        ).fetchone()
        return int(row[0])

    def indexed_files(self, model: str) -> int:
        """Files whose chunks all have a vector for the model."""
        row = self._db.execute(
            """
            SELECT COUNT(*)
            FROM files AS f
            WHERE NOT EXISTS (
                SELECT 1
                FROM chunk_meta AS m
                LEFT JOIN chunk_vectors AS v
                    ON v.rowid = m.rowid AND v.model = ?
                WHERE m.path = f.path AND v.rowid IS NULL
            )
            """,
            (model,),
        ).fetchone()
        return int(row[0])

    def chunks_without_vectors(
        self,
        model: str,
        *,
        limit: int,
    ) -> list[tuple[int, str, str, str, str, str, str]]:
        """Rows of (rowid, path, symbol, kind, signature, comment, body)."""
        return self._db.execute(
            """
            SELECT
                m.rowid,
                m.path,
                m.symbol,
                m.kind,
                m.signature,
                m.comment,
                m.body_text
            FROM chunk_meta AS m
            LEFT JOIN chunk_vectors AS v
                ON v.rowid = m.rowid AND v.model = ?
            WHERE v.rowid IS NULL
            ORDER BY m.rowid
            LIMIT ?
            """,
            (model, limit),
        ).fetchall()

    def save_vectors(
        self,
        model: str,
        rows: Sequence[tuple[int, bytes]],
    ) -> None:
        self._db.executemany(
            """
            INSERT OR REPLACE INTO chunk_vectors(
                rowid, model, dim, vector
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (rowid, model, len(blob) // 4, blob)
                for rowid, blob in rows
            ],
        )
        self._db.commit()

    def checkpoint(self) -> None:
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def drop_vectors(self) -> None:
        self._db.execute("DELETE FROM chunk_vectors")
        self._db.commit()

    def search(
        self,
        terms: list[str],
        limit: int = 10,
        *,
        language: str | None = None,
        path_prefix: str | None = None,
        test_penalty: float = 0.0,
    ) -> list[SearchHit]:
        if not terms:
            return []
        clauses = ["chunk_fts MATCH ?"]
        params: list[object] = [
            " OR ".join(f'"{term}"' for term in terms)
        ]
        if language:
            clauses.append("m.language = ?")
            params.append(language)
        if path_prefix:
            clauses.append("substr(m.path, 1, ?) = ?")
            params.extend([len(path_prefix), path_prefix])
        params.append(limit)

        score_expression = "bm25(chunk_fts, 5.0, 2.0, 2.0, 1.0)"
        if test_penalty > 0:
            score_expression += (
                " + CASE WHEN"
                " m.path LIKE 'tests/%'"
                " OR m.path LIKE '%/tests/%'"
                " OR m.path LIKE '__tests__/%'"
                " OR m.path LIKE '%/__tests__/%'"
                f" THEN {float(test_penalty)} ELSE 0 END"
            )

        rows = self._db.execute(
            f"""
            SELECT
                m.path,
                m.symbol,
                m.kind,
                m.start_line,
                m.end_line,
                {score_expression} AS score,
                m.language,
                m.signature,
                m.comment,
                m.rowid
            FROM chunk_fts
            JOIN chunk_meta AS m ON m.rowid = chunk_fts.rowid
            WHERE {' AND '.join(clauses)}
            ORDER BY score
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            SearchHit(
                path=row[0],
                symbol=row[1],
                kind=row[2],
                start_line=row[3],
                end_line=row[4],
                score=-float(row[5]),
                language=row[6],
                signature=row[7],
                comment=row[8],
                rowid=row[9],
            )
            for row in rows
        ]

    def search_vectors(
        self,
        query: Sequence[float],
        limit: int = 10,
        *,
        model: str,
        language: str | None = None,
        path_prefix: str | None = None,
        test_penalty: float = 0.0,
    ) -> list[SearchHit]:
        clauses = ["v.model = ?"]
        params: list[object] = [model]
        if language:
            clauses.append("m.language = ?")
            params.append(language)
        if path_prefix:
            clauses.append("substr(m.path, 1, ?) = ?")
            params.extend([len(path_prefix), path_prefix])

        rows = self._db.execute(
            f"""
            SELECT
                m.path,
                m.symbol,
                m.kind,
                m.start_line,
                m.end_line,
                m.language,
                m.signature,
                m.comment,
                m.rowid,
                v.vector
            FROM chunk_vectors AS v
            JOIN chunk_meta AS m ON m.rowid = v.rowid
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()
        if not rows:
            return []

        scores = cosine_similarities(
            query,
            [row[9] for row in rows],
        )
        ranked: list[tuple[float, tuple]] = []
        for row, score in zip(rows, scores):
            if test_penalty > 0 and _is_test_path(row[0]):
                score -= test_penalty
            ranked.append((score, row))
        ranked.sort(key=lambda item: (-item[0], item[1][0], item[1][3]))

        return [
            SearchHit(
                path=row[0],
                symbol=row[1],
                kind=row[2],
                start_line=row[3],
                end_line=row[4],
                score=score,
                language=row[5],
                signature=row[6],
                comment=row[7],
                rowid=row[8],
            )
            for score, row in ranked[:limit]
        ]

    def count(self) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM chunk_meta"
        ).fetchone()
        return int(row[0])

    def count_files(self) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._db.close()

    def _insert_chunk(self, chunk: Chunk) -> int:
        cursor = self._db.execute(
            """
            INSERT INTO chunk_fts(
                symbol_text, signature_text,
                comment_text, body_text
            ) VALUES (?, ?, ?, ?)
            """,
            (
                split_identifier_text(chunk.symbol),
                split_identifier_text(chunk.signature),
                split_identifier_text(chunk.comment),
                split_identifier_text(chunk.body),
            ),
        )
        rowid = int(cursor.lastrowid or 0)
        self._db.execute(
            """
            INSERT INTO chunk_meta(
                rowid, path, language, symbol, kind,
                start_line, end_line, signature,
                comment, content_hash, body_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rowid,
                chunk.path,
                chunk.language,
                chunk.symbol,
                chunk.kind,
                chunk.start_line,
                chunk.end_line,
                chunk.signature,
                chunk.comment,
                chunk.content_hash,
                chunk.body,
            ),
        )
        return rowid


def _is_test_path(path: str) -> bool:
    prefixed = f"/{path}"
    return any(
        f"/{part}/" in prefixed
        for part in ("tests", "test", "__tests__")
    )


__all__ = [
    "Bm25Index",
    "SCHEMA_VERSION",
    "SearchHit",
    "split_identifier_text",
    "tokenize_query",
]

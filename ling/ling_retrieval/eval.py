"""Offline BM25 vs grep evaluation for a Python workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import tempfile
import time
from pathlib import Path

from .bm25 import Bm25Index, SearchHit, tokenize_query
from .chunking import Chunk, chunk_files, iter_source_files
from .embedding import OpenAIEmbedder, embedding_text
from .vectors import pack_vector, reciprocal_rank_fusion


def load_queries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("queries file must contain a JSON array")
    return data


def relevant_keys(query: dict) -> set[tuple[str, str | None]]:
    keys: set[tuple[str, str | None]] = set()
    for item in query.get("relevant", []):
        path = item.get("path")
        if not path:
            continue
        keys.add((path, item.get("symbol")))
    return keys


def is_relevant(
    hit: SearchHit,
    keys: set[tuple[str, str | None]],
) -> bool:
    return (hit.path, hit.symbol) in keys or (
        hit.path,
        None,
    ) in keys


def first_rank(
    hits: list[SearchHit],
    keys: set[tuple[str, str | None]],
) -> int | None:
    for rank, hit in enumerate(hits, 1):
        if is_relevant(hit, keys):
            return rank
    return None


def recall_at(
    hits: list[SearchHit],
    keys: set[tuple[str, str | None]],
    k: int,
) -> float:
    if not keys:
        return 0.0
    found: set[tuple[str, str | None]] = set()
    for hit in hits[:k]:
        if (hit.path, hit.symbol) in keys:
            found.add((hit.path, hit.symbol))
        if (hit.path, None) in keys:
            found.add((hit.path, None))
    return len(found) / len(keys)


def mrr_at(
    hits: list[SearchHit],
    keys: set[tuple[str, str | None]],
    k: int = 10,
) -> float:
    rank = first_rank(hits[:k], keys)
    return 0.0 if rank is None else 1.0 / rank


def is_test_path(path: Path) -> bool:
    return any(
        part in {"tests", "test", "__tests__"}
        for part in path.parts
    )


def load_file_texts(
    root: Path,
    files: list[Path] | None = None,
) -> dict[str, str]:
    selected = files if files is not None else iter_source_files(root)
    texts: dict[str, str] = {}
    for path in selected:
        try:
            relative = path.relative_to(root).as_posix()
            texts[relative] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return texts


def grep_hits(
    chunks: list[Chunk],
    texts: dict[str, str],
    terms: list[str],
    limit: int = 10,
) -> list[SearchHit]:
    if not terms:
        return []

    pattern = re.compile(
        "|".join(re.escape(term) for term in terms),
        re.IGNORECASE,
    )
    by_path: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_path.setdefault(chunk.path, []).append(chunk)

    scores: dict[str, dict] = {}
    for path, text in texts.items():
        file_chunks = by_path.get(path)
        if not file_chunks:
            continue
        for line_number, line in enumerate(
            text.splitlines(),
            start=1,
        ):
            if not pattern.search(line):
                continue
            chunk = _smallest_chunk(file_chunks, line_number)
            if chunk is None:
                continue
            entry = scores.setdefault(
                chunk.chunk_id,
                {"chunk": chunk, "terms": set(), "count": 0},
            )
            entry["count"] += 1
            for term in terms:
                if re.search(
                    re.escape(term),
                    line,
                    re.IGNORECASE,
                ):
                    entry["terms"].add(term)

    ordered = sorted(
        scores.values(),
        key=lambda item: (
            -len(item["terms"]),
            -item["count"],
            item["chunk"].path,
            item["chunk"].start_line,
        ),
    )[:limit]
    return [
        SearchHit(
            path=item["chunk"].path,
            symbol=item["chunk"].symbol,
            kind=item["chunk"].kind,
            start_line=item["chunk"].start_line,
            end_line=item["chunk"].end_line,
            score=float(
                len(item["terms"]) * 100 + item["count"]
            ),
        )
        for item in ordered
    ]


def _smallest_chunk(
    chunks: list[Chunk],
    line_number: int,
) -> Chunk | None:
    containing = [
        chunk
        for chunk in chunks
        if chunk.start_line <= line_number <= chunk.end_line
    ]
    if not containing:
        return None
    return min(
        containing,
        key=lambda chunk: (
            chunk.end_line - chunk.start_line,
            chunk.start_line,
        ),
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _format_rank(rank: int | None) -> str:
    return "-" if rank is None else str(rank)


def run(args: argparse.Namespace) -> int:
    root = args.workspace.resolve()
    if not root.is_dir():
        raise SystemExit(f"workspace is not a directory: {root}")

    queries = load_queries(args.queries)
    files = [
        path
        for path in iter_source_files(root)
        if args.include_tests
        or not is_test_path(path.relative_to(root))
    ]
    chunk_started = time.perf_counter()
    chunks = chunk_files(root, files)
    chunk_seconds = time.perf_counter() - chunk_started
    texts = load_file_texts(root, files)

    embedder = _embedder_from_args(args)
    query_vectors: list[list[float]] = []
    if embedder is not None:
        query_vectors = embedder.embed(
            [str(query.get("query", "")) for query in queries]
        )

    with tempfile.TemporaryDirectory() as temp:
        db_path = Path(temp) / "index.sqlite"
        index = Bm25Index(db_path)
        build_started = time.perf_counter()
        index.add(chunks)
        build_seconds = time.perf_counter() - build_started

        embed_started = time.perf_counter()
        if embedder is not None:
            pending = index.chunks_without_vectors(
                embedder.model,
                limit=len(chunks),
            )
            vectors = embedder.embed(
                [
                    embedding_text(
                        kind=row[3],
                        symbol=row[2],
                        signature=row[4],
                        comment=row[5],
                        body=row[6],
                    )
                    for row in pending
                ]
            )
            index.save_vectors(
                embedder.model,
                [
                    (row[0], pack_vector(vector))
                    for row, vector in zip(pending, vectors)
                ],
            )
        embed_seconds = time.perf_counter() - embed_started
        index.checkpoint()
        index_bytes = db_path.stat().st_size

        bm25_times: list[float] = []
        grep_times: list[float] = []
        vector_times: list[float] = []
        rows: list[dict] = []
        bm25_recall5: list[float] = []
        bm25_recall10: list[float] = []
        bm25_mrr: list[float] = []
        grep_recall5: list[float] = []
        grep_recall10: list[float] = []
        grep_mrr: list[float] = []
        vector_recall5: list[float] = []
        vector_recall10: list[float] = []
        vector_mrr: list[float] = []
        hybrid_recall5: list[float] = []
        hybrid_recall10: list[float] = []
        hybrid_mrr: list[float] = []

        for query_index, query in enumerate(queries):
            text = str(query.get("query", ""))
            keys = relevant_keys(query)
            terms = tokenize_query(text)

            started = time.perf_counter()
            bm25_hits = index.search(terms, limit=args.top_k)
            bm25_times.append((time.perf_counter() - started) * 1000)

            started = time.perf_counter()
            grep_results = grep_hits(
                chunks,
                texts,
                terms,
                limit=args.top_k,
            )
            grep_times.append((time.perf_counter() - started) * 1000)

            bm25_rank = first_rank(bm25_hits, keys)
            grep_rank = first_rank(grep_results, keys)
            bm25_recall5.append(recall_at(bm25_hits, keys, 5))
            bm25_recall10.append(recall_at(bm25_hits, keys, 10))
            bm25_mrr.append(mrr_at(bm25_hits, keys))
            grep_recall5.append(recall_at(grep_results, keys, 5))
            grep_recall10.append(recall_at(grep_results, keys, 10))
            grep_mrr.append(mrr_at(grep_results, keys))

            row: dict = {
                "query": text,
                "terms": terms,
                "relevant": sorted(
                    f"{path}:{symbol or '*'}"
                    for path, symbol in keys
                ),
                "bm25Rank": bm25_rank,
                "grepRank": grep_rank,
                "bm25Top": _hit_labels(bm25_hits[:5]),
                "grepTop": _hit_labels(grep_results[:5]),
            }

            if embedder is not None:
                started = time.perf_counter()
                vector_hits = index.search_vectors(
                    query_vectors[query_index],
                    args.top_k,
                    model=embedder.model,
                )
                vector_times.append(
                    (time.perf_counter() - started) * 1000
                )
                hybrid_hits = _fuse_hits(
                    bm25_hits,
                    vector_hits,
                    args.top_k,
                )
                vector_recall5.append(
                    recall_at(vector_hits, keys, 5)
                )
                vector_recall10.append(
                    recall_at(vector_hits, keys, 10)
                )
                vector_mrr.append(mrr_at(vector_hits, keys))
                hybrid_recall5.append(
                    recall_at(hybrid_hits, keys, 5)
                )
                hybrid_recall10.append(
                    recall_at(hybrid_hits, keys, 10)
                )
                hybrid_mrr.append(mrr_at(hybrid_hits, keys))
                row["vectorRank"] = first_rank(vector_hits, keys)
                row["hybridRank"] = first_rank(hybrid_hits, keys)
                row["vectorTop"] = _hit_labels(vector_hits[:5])

            rows.append(row)

        index.close()

    print(f"workspace: {root}")
    print(
        f"files: {len(files)}, chunks: {len(chunks)}"
    )
    print(
        f"chunk: {chunk_seconds:.3f}s, "
        f"build: {build_seconds:.3f}s, "
        f"index: {index_bytes / 1024:.1f} KiB"
    )
    if embedder is not None:
        print(
            f"embedding: {embedder.model}, "
            f"build: {embed_seconds:.3f}s"
        )
    print()
    if embedder is not None:
        print(
            f"{'query':40} {'BM25':>5} {'grep':>5} "
            f"{'vec':>5} {'RRF':>5}"
        )
        for row in rows:
            print(
                f"{row['query'][:38]:40} "
                f"{_format_rank(row['bm25Rank']):>5} "
                f"{_format_rank(row['grepRank']):>5} "
                f"{_format_rank(row['vectorRank']):>5} "
                f"{_format_rank(row['hybridRank']):>5}"
            )
    else:
        print(f"{'query':48} {'BM25':>5} {'grep':>5}")
        for row in rows:
            print(
                f"{row['query'][:46]:48} "
                f"{_format_rank(row['bm25Rank']):>5} "
                f"{_format_rank(row['grepRank']):>5}"
            )
    print()
    print(
        "BM25  "
        f"recall@5={_mean(bm25_recall5):.3f} "
        f"recall@10={_mean(bm25_recall10):.3f} "
        f"MRR@10={_mean(bm25_mrr):.3f} "
        f"p50={statistics.median(bm25_times):.2f}ms"
    )
    print(
        "grep  "
        f"recall@5={_mean(grep_recall5):.3f} "
        f"recall@10={_mean(grep_recall10):.3f} "
        f"MRR@10={_mean(grep_mrr):.3f} "
        f"p50={statistics.median(grep_times):.2f}ms"
    )
    if embedder is not None:
        print(
            "vector "
            f"recall@5={_mean(vector_recall5):.3f} "
            f"recall@10={_mean(vector_recall10):.3f} "
            f"MRR@10={_mean(vector_mrr):.3f} "
            f"p50={statistics.median(vector_times):.2f}ms"
        )
        print(
            "RRF   "
            f"recall@5={_mean(hybrid_recall5):.3f} "
            f"recall@10={_mean(hybrid_recall10):.3f} "
            f"MRR@10={_mean(hybrid_mrr):.3f}"
        )

    if args.out:
        report = {
            "workspace": str(root),
            "files": len(files),
            "chunks": len(chunks),
            "chunkSeconds": chunk_seconds,
            "buildSeconds": build_seconds,
            "indexBytes": index_bytes,
            "topK": args.top_k,
            "bm25": {
                "recall@5": _mean(bm25_recall5),
                "recall@10": _mean(bm25_recall10),
                "mrr@10": _mean(bm25_mrr),
                "p50Ms": statistics.median(bm25_times),
            },
            "grep": {
                "recall@5": _mean(grep_recall5),
                "recall@10": _mean(grep_recall10),
                "mrr@10": _mean(grep_mrr),
                "p50Ms": statistics.median(grep_times),
            },
            "queries": rows,
        }
        if embedder is not None:
            report["embedding"] = {
                "model": embedder.model,
                "seconds": embed_seconds,
            }
            report["vector"] = {
                "recall@5": _mean(vector_recall5),
                "recall@10": _mean(vector_recall10),
                "mrr@10": _mean(vector_mrr),
                "p50Ms": statistics.median(vector_times),
            }
            report["hybrid"] = {
                "recall@5": _mean(hybrid_recall5),
                "recall@10": _mean(hybrid_recall10),
                "mrr@10": _mean(hybrid_mrr),
            }
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nreport: {args.out}")

    return 0


def _hit_labels(hits: list[SearchHit]) -> list[str]:
    return [f"{hit.path}:{hit.symbol}" for hit in hits]


def _fuse_hits(
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
    by_id.update({hit.rowid: hit for hit in vector_hits})
    return [
        by_id[rowid]
        for rowid, _ in fused
        if rowid in by_id
    ]


def _embedder_from_args(
    args: argparse.Namespace,
) -> OpenAIEmbedder | None:
    model = (args.embedding_model or "").strip()
    if not model:
        return None
    api_key = (
        (args.embedding_api_key or "").strip()
        or os.environ.get("LING_API_KEY", "").strip()
        or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        raise SystemExit(
            "--embedding-model requires --embedding-api-key "
            "or LING_API_KEY"
        )
    base_url = (
        (args.embedding_base_url or "").strip()
        or os.environ.get("LING_API_BASE_URL", "").strip()
    )
    if not base_url:
        raise SystemExit(
            "--embedding-model requires --embedding-base-url "
            "or LING_API_BASE_URL"
        )
    return OpenAIEmbedder(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare BM25 and grep on a Python workspace.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=Path(__file__).with_name("eval_queries.json"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("LING_EMBEDDING_MODEL", ""),
    )
    parser.add_argument(
        "--embedding-base-url",
        default=os.environ.get("LING_EMBEDDING_BASE_URL", ""),
    )
    parser.add_argument(
        "--embedding-api-key",
        default=os.environ.get("LING_EMBEDDING_API_KEY", ""),
    )
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()

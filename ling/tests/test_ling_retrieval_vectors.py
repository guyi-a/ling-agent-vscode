"""Tests for vector storage and hybrid retrieval."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Sequence

import httpx
import pytest

from ling_agent import Workspace
from ling_agent.tools.search import create_search_tools
from ling_ai.types import ToolCall
from ling_core import ToolContext
from ling_retrieval.bm25 import Bm25Index
from ling_retrieval.chunking import Chunk
from ling_retrieval.embedding import (
    EmbeddingError,
    OpenAIEmbedder,
    embedding_text,
)
from ling_retrieval.index import CodeIndex
from ling_retrieval.vectors import (
    cosine_similarities,
    pack_vector,
    reciprocal_rank_fusion,
    unpack_vector,
    vector_dimensions,
)


class FakeEmbedder:
    """Deterministic embedder keyed on two topic words."""

    def __init__(self, model: str = "fake-embed") -> None:
        self.model = model
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        return [
            1.0 if "skill" in lowered else 0.0,
            1.0 if "render" in lowered else 0.0,
            0.1,
        ]


class FailingEmbedder:
    model = "failing"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("embedding provider is down")


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_pack_vector_normalizes_and_round_trips() -> None:
    blob = pack_vector([3.0, 4.0])
    assert vector_dimensions(blob) == 2
    values = unpack_vector(blob)
    assert values[0] == pytest.approx(0.6, abs=1e-6)
    assert values[1] == pytest.approx(0.8, abs=1e-6)


def test_cosine_similarities_orders_vectors() -> None:
    blobs = [
        pack_vector([1.0, 0.0, 0.0]),
        pack_vector([0.0, 1.0, 0.0]),
        pack_vector([0.9, 0.1, 0.0]),
    ]
    scores = cosine_similarities([1.0, 0.0, 0.0], blobs)
    assert scores[0] == pytest.approx(1.0, abs=1e-6)
    assert scores[2] > scores[1]


def test_reciprocal_rank_fusion_prefers_shared_items() -> None:
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 4, 1]], limit=3)
    assert [item[0] for item in fused[:2]] == [1, 3]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 63)


def test_embedding_text_includes_signature_and_body() -> None:
    text = embedding_text(
        kind="function",
        symbol="load_skill",
        signature="def load_skill(name)",
        comment="Load a skill.",
        body="def load_skill(name):\n    return name",
    )
    assert text.startswith("function load_skill")
    assert "def load_skill(name)" in text
    assert "Load a skill." in text


def _clear_embedding_env(monkeypatch) -> None:
    for name in (
        "LING_EMBEDDING_MODEL",
        "LING_EMBEDDING_BASE_URL",
        "LING_EMBEDDING_API_KEY",
        "LING_API_BASE_URL",
        "LING_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_resolve_embedder_requires_key_for_other_provider(
    monkeypatch,
) -> None:
    from ling_rpc.provider import resolve_embedder

    _clear_embedding_env(monkeypatch)
    monkeypatch.setenv("LING_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv(
        "LING_EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    monkeypatch.setenv("LING_API_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LING_API_KEY", "sk-chat")

    assert resolve_embedder() is None

    monkeypatch.setenv("LING_EMBEDDING_API_KEY", "sk-embed")
    embedder = resolve_embedder()
    assert embedder is not None
    assert embedder.model == "text-embedding-v4"
    embedder.close()


def test_resolve_embedder_reuses_key_for_same_provider(
    monkeypatch,
) -> None:
    from ling_rpc.provider import resolve_embedder

    _clear_embedding_env(monkeypatch)
    monkeypatch.setenv("LING_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv(
        "LING_EMBEDDING_BASE_URL",
        "https://api.deepseek.com/v1",
    )
    monkeypatch.setenv(
        "LING_API_BASE_URL",
        "https://api.deepseek.com/v1/",
    )
    monkeypatch.setenv("LING_API_KEY", "sk-chat")

    embedder = resolve_embedder()
    assert embedder is not None
    embedder.close()


def test_openai_embedder_batches_requests() -> None:
    payloads: list[dict] = []
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index), 1.0]}
                    for index in range(len(payload["input"]))
                ]
            },
        )

    embedder = OpenAIEmbedder(
        model="model-x",
        api_key="sk-test",
        base_url="https://example.test/v1",
        batch_size=2,
        client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )
    vectors = embedder.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert [len(payload["input"]) for payload in payloads] == [2, 1]
    assert payloads[0]["model"] == "model-x"
    assert payloads[0]["encoding_format"] == "float"
    assert vectors[0] == [0.0, 1.0]
    assert str(requests[0].url) == "https://example.test/v1/embeddings"
    assert requests[0].headers["authorization"] == "Bearer sk-test"


def test_openai_embedder_shrinks_batch_on_400() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sizes.append(len(payload["input"]))
        if len(payload["input"]) > 1:
            return httpx.Response(
                400,
                json={"error": "batch too large"},
            )
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0]}]},
        )

    embedder = OpenAIEmbedder(
        model="model-x",
        api_key="sk-test",
        base_url="https://example.test/v1",
        batch_size=4,
        client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )
    vectors = embedder.embed(["a", "b", "c", "d"])

    assert len(vectors) == 4
    assert sizes[0] == 4
    assert sizes[1] == 2
    assert max(sizes[2:]) == 1


def test_bm25_index_tracks_missing_vectors() -> None:
    first = Chunk(
        path="a.py",
        language="python",
        kind="function",
        symbol="load_skill",
        start_line=1,
        end_line=2,
        signature="def load_skill()",
        comment="",
        body="def load_skill():\n    return 1",
    )
    second = Chunk(
        path="a.py",
        language="python",
        kind="function",
        symbol="render",
        start_line=4,
        end_line=5,
        signature="def render()",
        comment="",
        body="def render():\n    return 2",
    )
    index = Bm25Index()
    try:
        assert index.vector_model() is None
        assert len(index.chunks_without_vectors("m", limit=10)) == 0

        index.upsert_file("a.py", 1, 10, "python", [first, second])
        pending = index.chunks_without_vectors("m", limit=10)
        assert [row[2] for row in pending] == [
            "load_skill",
            "render",
        ]

        index.save_vectors(
            "m",
            [(pending[0][0], pack_vector([1.0, 0.0]))],
        )
        assert index.vector_model() == "m"
        assert index.count_vectors("m") == 1
        assert len(index.chunks_without_vectors("m", limit=10)) == 1

        # Rewriting the file keeps the vector for the unchanged chunk.
        index.save_vectors(
            "m",
            [(pending[1][0], pack_vector([0.0, 1.0]))],
        )
        assert index.count_vectors("m") == 2
        assert index.chunks_without_vectors("m", limit=10) == []
    finally:
        index.close()


def test_code_index_hybrid_search() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        _write(
            root,
            "render.py",
            "def render_page(page):\n    return page\n",
        )
        embedder = FakeEmbedder()
        index = CodeIndex(root)

        results = index.search("show skill", embedder=embedder)
        assert index.last_search_mode == "hybrid"
        assert results
        assert results[0]["path"] == "skills.py"

        results = index.search("how", embedder=embedder)
        assert results

        plain = CodeIndex(root)
        plain.search("show skill")
        assert plain.last_search_mode == "bm25"


def test_unchanged_chunks_are_not_reembedded() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "a.py",
            "def alpha():\n    return 1\n"
            "\n"
            "def beta():\n    return 2\n",
        )
        embedder = FakeEmbedder()
        index = CodeIndex(root)
        index.search("alpha", embedder=embedder)
        assert [len(batch) for batch in embedder.calls] == [2, 1]

        _write(
            root,
            "a.py",
            "def alpha():\n    value = 1\n    return value\n"
            "\n"
            "def beta():\n    return 2\n",
        )
        index.search("alpha", embedder=embedder)
        assert [len(batch) for batch in embedder.calls] == [
            2,
            1,
            1,
            1,
        ]


def test_new_model_drops_old_vectors() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        first = FakeEmbedder("model-a")
        CodeIndex(root).search("skill", embedder=first)

        second = FakeEmbedder("model-b")
        CodeIndex(root).search("skill", embedder=second)
        assert len(second.calls[0]) == 1


def test_code_index_build_is_incremental() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        _write(
            root,
            "render.py",
            "def render_page(page):\n    return page\n",
        )
        embedder = FakeEmbedder()

        stats = CodeIndex(root).build(embedder=embedder)
        assert stats["files"] == 2
        assert stats["totalFiles"] == 2
        assert stats["chunks"] == 2
        assert stats["vectors"] == 2
        assert stats["indexedFiles"] == 2
        assert stats["percent"] == 100
        assert stats["embedded"] == 2
        assert stats["model"] == "fake-embed"
        assert "error" not in stats

        stats = CodeIndex(root).build(embedder=embedder)
        assert stats["vectors"] == 2
        assert stats["embedded"] == 0

        _write(
            root,
            "skills.py",
            "def load_skill_body(name):\n    return name\n",
        )
        stats = CodeIndex(root).build(embedder=embedder)
        assert stats["embedded"] == 1
        assert stats["vectors"] == 2


def test_code_index_status_reports_progress() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        _write(
            root,
            "render.py",
            "def render_page(page):\n    return page\n",
        )
        embedder = FakeEmbedder()
        index = CodeIndex(root)

        status = index.status(embedder=embedder)
        assert status["files"] == 0
        assert status["totalFiles"] == 2
        assert status["percent"] == 0
        assert status["model"] == "fake-embed"

        updates: list[dict] = []
        index.build(embedder=embedder, progress=updates.append)
        assert updates
        assert updates[-1]["percent"] == 100
        assert updates[-1]["indexedFiles"] == 2

        status = index.status(embedder=embedder)
        assert status["indexedFiles"] == 2
        assert status["vectors"] == 2
        assert status["percent"] == 100

        keyword = index.status()
        assert keyword["model"] is None
        assert keyword["indexedFiles"] == 2
        assert keyword["percent"] == 100


def test_code_index_build_without_embedder() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        stats = CodeIndex(root).build()
        assert stats["files"] == 1
        assert stats["chunks"] == 1
        assert stats["model"] is None
        assert stats["vectors"] == 0
        assert stats["embedded"] == 0


def test_code_index_build_reports_embedding_error() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        stats = CodeIndex(root).build(embedder=FailingEmbedder())
        assert stats["chunks"] == 1
        assert stats["vectors"] == 0
        assert "embedding provider is down" in stats["error"]


def test_embedding_failure_falls_back_to_bm25() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        index = CodeIndex(root)
        results = index.search(
            "load skill",
            embedder=FailingEmbedder(),
        )
        assert index.last_search_mode == "bm25"
        assert results
        assert results[0]["symbol"] == "load_skill"


def test_search_codebase_tool_reports_mode() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        tools = create_search_tools(
            Workspace(root),
            embedder=FakeEmbedder(),
        )
        tool = next(
            item
            for item in tools
            if item.definition.name == "search_codebase"
        )
        result = asyncio.run(
            tool.handler(
                ToolCall(
                    id="call-1",
                    name="search_codebase",
                    arguments={"query": "show skill"},
                ),
                ToolContext(step=1),
            )
        )

        payload = json.loads(result)
        assert payload["mode"] == "hybrid"
        assert payload["results"][0]["symbol"] == "load_skill"

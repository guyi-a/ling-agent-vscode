"""Tests for the offline retrieval prototype."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from ling_agent import Workspace
from ling_agent.tools.search import create_search_tools
from ling_ai.types import ToolCall
from ling_core import ToolContext
from ling_retrieval.bm25 import (
    Bm25Index,
    split_identifier_text,
    tokenize_query,
)
from ling_retrieval.chunking import Chunk, chunk_file
from ling_retrieval.index import CodeIndex


def test_split_identifier_text() -> None:
    assert split_identifier_text("parsePDFPage") == "parse pdf page"
    assert (
        split_identifier_text("XMLHttpRequest")
        == "xml http request"
    )
    assert split_identifier_text("read_file") == "read file"
    assert tokenize_query("parse the PDF page") == [
        "parse",
        "pdf",
        "page",
    ]


def test_chunk_python_file() -> None:
    source = (
        "\n"
        "# Leading comment.\n"
        "@decorator\n"
        "def load_skill(name: str) -> str:\n"
        '    """Load a skill."""\n'
        "    return name\n"
        "\n"
        "\n"
        "class Skill:\n"
        '    """A skill."""\n'
        "\n"
        "    def render(self) -> str:\n"
        "        return self.name\n"
    )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / "skills.py"
        path.write_text(source, encoding="utf-8")
        chunks = chunk_file(path, root)

    assert [(chunk.symbol, chunk.kind) for chunk in chunks] == [
        ("load_skill", "function"),
        ("Skill", "class"),
        ("render", "method"),
    ]
    function = chunks[0]
    assert function.start_line == 3
    assert function.signature.startswith("def load_skill(")
    assert "Leading comment" in function.comment
    assert "Load a skill." in function.comment
    assert function.path == "skills.py"


def test_chunk_typescript_file() -> None:
    source = (
        "/** Adds two numbers. */\n"
        "export function add(\n"
        "  a: number,\n"
        "  b: number,\n"
        "): number {\n"
        "  return a + b;\n"
        "}\n"
        "\n"
        "export class Service {\n"
        "  /** Runs the service. */\n"
        "  async run(value: string): Promise<void> {\n"
        "    return;\n"
        "  }\n"
        "}\n"
        "\n"
        "export const double = (value: number) => value * 2;\n"
    )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / "service.ts"
        path.write_text(source, encoding="utf-8")
        chunks = chunk_file(path, root)

    assert [(chunk.symbol, chunk.kind) for chunk in chunks] == [
        ("add", "function"),
        ("Service", "class"),
        ("run", "method"),
        ("double", "function"),
    ]
    function = chunks[0]
    assert function.start_line == 1
    assert "Adds two numbers" in function.comment
    assert function.signature.startswith("export function add(")
    assert function.signature.endswith("): number")
    method = chunks[2]
    assert "Runs the service" in method.comment
    assert method.kind == "method"


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_code_index_incremental_update() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "pkg/skills.py",
            "def load_skill(name):\n    return name\n",
        )
        _write(
            root,
            "pkg/render.ts",
            "export function render() { return 1; }\n",
        )
        index = CodeIndex(root)

        results = index.search("load skill")
        assert results
        assert results[0]["path"] == "pkg/skills.py"
        assert results[0]["symbol"] == "load_skill"
        assert "def load_skill" in results[0]["preview"]

        assert index.search(
            "load skill",
            language="typescript",
        ) == []
        assert index.search(
            "load skill",
            path_prefix="other/",
        ) == []

        _write(
            root,
            "pkg/skills.py",
            "def load_skill_body(name):\n    return name\n",
        )
        results = index.search("load skill body")
        assert results
        assert results[0]["symbol"] == "load_skill_body"

        (root / "pkg/render.ts").unlink()
        assert index.search("render page") == []


def test_search_codebase_tool() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(
            root,
            "skills.py",
            "def load_skill(name):\n    return name\n",
        )
        tools = create_search_tools(Workspace(root))
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
                    arguments={"query": "load skill"},
                ),
                ToolContext(step=1),
            )
        )

        assert isinstance(result, str)
        payload = json.loads(result)
        assert payload["results"][0]["symbol"] == "load_skill"


def test_bm25_search_finds_symbol() -> None:
    chunks = [
        Chunk(
            path="skills.py",
            language="python",
            kind="function",
            symbol="load_skill",
            start_line=1,
            end_line=3,
            signature="def load_skill(name)",
            comment="Load skill body",
            body="def load_skill(name):\n    return name",
        ),
        Chunk(
            path="render.py",
            language="python",
            kind="function",
            symbol="render_page",
            start_line=1,
            end_line=3,
            signature="def render_page(page)",
            comment="Render a page",
            body="def render_page(page):\n    return page",
        ),
    ]
    index = Bm25Index()
    try:
        index.add(chunks)
        hits = index.search(tokenize_query("load skill"), limit=5)
    finally:
        index.close()

    assert hits
    assert hits[0].symbol == "load_skill"

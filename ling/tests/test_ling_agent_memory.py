"""Tests for durable project memories and dynamic prompt injection."""

from __future__ import annotations

import asyncio
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ling_agent import (
    MemoryStore,
    Workspace,
    build_system_prompt,
    load_memories,
)
from ling_agent.tools.memory import create_memory_tools
from ling_ai.types import ToolCall
from ling_core import ToolContext
from ling_rpc.server import JsonRpcServer


def _write_memory(
    root: Path,
    name: str,
    *,
    description: str = "Project guidance",
    always_apply: bool,
    body: str = "Follow the project convention.",
    globs: tuple[str, ...] = (),
    directory: str = ".agents/memory",
) -> None:
    path = root / directory / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                f"alwaysApply: {'true' if always_apply else 'false'}",
                f"globs: {list(globs)!r}".replace("'", '"'),
                "---",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_loads_only_always_apply_memories() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_memory(root, "always", always_apply=True)
        _write_memory(
            root,
            "scoped",
            always_apply=False,
            body="Only use this memory for TypeScript files.",
        )

        result = load_memories(root)

        assert [memory.name for memory in result.memories] == ["always"]
        assert (
            result.memories[0].relative_path
            == ".agents/memory/always.md"
        )
        assert result.diagnostics == ()


def test_invalid_memory_reports_diagnostic() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_memory(
            root,
            "invalid",
            description="",
            always_apply=True,
        )

        result = load_memories(root)

        assert result.memories == ()
        assert len(result.diagnostics) == 1
        assert "description is required" in result.diagnostics[0]


def test_memories_are_loaded_dynamically_into_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = Workspace(root)

        initial = build_system_prompt(workspace)
        assert "## Project memory" not in initial.system_prompt
        assert "memory" not in initial.sources

        _write_memory(
            root,
            "testing",
            always_apply=True,
            body="Run the focused test before the full suite.",
        )
        updated = build_system_prompt(workspace)

        assert "## Project memory" in updated.system_prompt
        assert (
            "Run the focused test before the full suite."
            in updated.system_prompt
        )
        assert ".agents/memory/testing.md" in updated.system_prompt
        assert "memory" in updated.sources


def test_global_memory_and_workspace_override() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        global_root = root / "global-memory"
        _write_memory(
            root,
            "style",
            always_apply=True,
            body="Use the workspace spelling.",
            directory=".agents/memory",
        )
        _write_memory(
            root,
            "style",
            always_apply=True,
            body="Use the global spelling.",
            directory="global-memory",
        )

        result = load_memories(root, global_root=global_root)

        assert len(result.memories) == 1
        assert result.memories[0].scope == "workspace"
        assert result.memories[0].body == "Use the workspace spelling."
        assert any("overrides global" in item for item in result.diagnostics)


def test_globs_activate_memory_for_matching_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_memory(
            root,
            "typescript",
            always_apply=False,
            globs=("**/*.ts", "**/*.tsx"),
            body="Run TypeScript checks.",
        )

        matched = load_memories(root, active_paths=("src/a.ts",))
        unmatched = load_memories(root, active_paths=("README.md",))

        assert [memory.name for memory in matched.memories] == [
            "typescript"
        ]
        assert unmatched.memories == ()


def test_globs_are_applied_during_prompt_assembly() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_memory(
            root,
            "typescript",
            always_apply=False,
            globs=("**/*.ts",),
            body="Run TypeScript checks.",
        )

        matched = build_system_prompt(
            Workspace(root),
            active_paths=("src/a.ts",),
        )
        unmatched = build_system_prompt(
            Workspace(root),
            active_paths=("README.md",),
        )

        assert "memory" in matched.sources
        assert "Run TypeScript checks." in matched.system_prompt
        assert "memory" not in unmatched.sources


def test_memory_store_crud_and_tools() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = MemoryStore(root, global_root=root / "global-memory")
        tools = {
            tool.definition.name: tool
            for tool in create_memory_tools(store)
        }
        call = ToolCall(
            id="memory-write",
            name="memory_write",
            arguments={
                "name": "project-style",
                "description": "Project style",
                "content": "Use concise Chinese.",
                "scope": "workspace",
                "always_apply": True,
            },
        )
        written = tools["memory_write"].handler(
            call,
            ToolContext(step=1),
        )
        assert written.is_error is False
        assert (root / ".agents/memory/project-style.md").is_file()

        listed = tools["memory_list"].handler(
            ToolCall(
                id="memory-list",
                name="memory_list",
                arguments={"scope": "workspace"},
            ),
            ToolContext(step=2),
        )
        assert '"name": "project-style"' in listed.output

        blocked = tools["memory_delete"].handler(
            ToolCall(
                id="memory-delete-no-confirm",
                name="memory_delete",
                arguments={
                    "name": "project-style",
                    "scope": "workspace",
                    "confirm": False,
                },
            ),
            ToolContext(step=3),
        )
        assert blocked.is_error is True

        deleted = tools["memory_delete"].handler(
            ToolCall(
                id="memory-delete",
                name="memory_delete",
                arguments={
                    "name": "project-style",
                    "scope": "workspace",
                    "confirm": True,
                },
            ),
            ToolContext(step=4),
        )
        assert deleted.is_error is False
        assert not (
            root / ".agents/memory/project-style.md"
        ).exists()


def test_memory_rpc_round_trip() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = JsonRpcServer(StringIO(), StringIO())
            with patch.dict(
                "os.environ",
                {
                    "LING_GLOBAL_MEMORY_ROOT": str(
                        root / "global-memory"
                    )
                },
            ):
                written = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "memory.write",
                        "params": {
                            "workspaceRoot": str(root),
                            "name": "rpc-memory",
                            "description": "RPC memory",
                            "content": "Remember through RPC.",
                            "scope": "workspace",
                            "alwaysApply": True,
                            "globs": [],
                        },
                    }
                )
                assert written is not None
                assert written["result"]["ok"] is True

                listed = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "memory.list",
                        "params": {"workspaceRoot": str(root)},
                    }
                )
                assert listed is not None
                assert listed["result"]["memories"][0]["name"] == (
                    "rpc-memory"
                )

                deleted = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "memory.delete",
                        "params": {
                            "workspaceRoot": str(root),
                            "name": "rpc-memory",
                            "scope": "workspace",
                            "confirm": True,
                        },
                    }
                )
                assert deleted is not None
                assert deleted["result"]["ok"] is True

    asyncio.run(run())


if __name__ == "__main__":
    test_loads_only_always_apply_memories()
    test_invalid_memory_reports_diagnostic()
    test_memories_are_loaded_dynamically_into_prompt()
    test_global_memory_and_workspace_override()
    test_globs_activate_memory_for_matching_paths()
    test_globs_are_applied_during_prompt_assembly()
    test_memory_store_crud_and_tools()
    test_memory_rpc_round_trip()
    print("all ling_agent memory tests passed")

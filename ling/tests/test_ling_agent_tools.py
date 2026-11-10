"""Tests for ling_agent read-only tools and core integration."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantContent,
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from ling_agent import (
    Workspace,
    build_system_prompt,
    create_file_tools,
    create_search_tools,
)
from ling_core import Agent, ToolContext
from ling_core.tools import ToolRegistry

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _assistant(
    content: list[AssistantContent],
    *,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
    )


class ScriptedStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)
        self.contexts: list[list[object]] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        self.contexts.append(list(context.messages))
        message = self.messages.pop(0)
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


async def _read_only_tools() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(root, "src/a.py", "alpha\nbeta\n")
        _write(root, "src/b.txt", "beta text\n")
        workspace = Workspace(root)
        registry = ToolRegistry(create_file_tools(workspace) + create_search_tools(workspace))
        context = ToolContext(step=1)

        read = await registry.execute(
            ToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "src/a.py"},
            ),
            context,
        )
        listing = await registry.execute(
            ToolCall(
                id="call_2",
                name="list_files",
                arguments={"path": "src"},
            ),
            context,
        )
        globbed = await registry.execute(
            ToolCall(
                id="call_3",
                name="glob",
                arguments={"pattern": "**/*.py"},
            ),
            context,
        )
        grepped = await registry.execute(
            ToolCall(
                id="call_4",
                name="grep",
                arguments={"pattern": "beta", "path": "src"},
            ),
            context,
        )

        assert read.content == [TextContent("alpha\nbeta")]
        assert listing.content[0].text == "src/a.py\nsrc/b.txt"
        assert globbed.content[0].text == "src/a.py"
        assert "src/a.py:2:beta" in grepped.content[0].text
        assert "src/b.txt:1:beta text" in grepped.content[0].text


def test_read_only_tools() -> None:
    asyncio.run(_read_only_tools())


def test_scripted_model_inspects_workspace_through_core() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write(root, "README.md", "project readme\n")
        workspace = Workspace(root)
        prompt = build_system_prompt(workspace)
        streams = ScriptedStreams(
            [
                _assistant(
                    [
                        ToolCall(
                            id="call_1",
                            name="read_file",
                            arguments={"path": "README.md"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                _assistant([TextContent("The README describes the project.")]),
            ]
        )
        agent = Agent(
            model=MODEL,
            stream_fn=streams,
            context=Context(system_prompt=prompt.system_prompt),
            tools=create_file_tools(workspace) + create_search_tools(workspace),
        )

        result = asyncio.run(agent.run("Inspect the README."))

        assert result.content == [
            TextContent("The README describes the project.")
        ]
        tool_results = [
            message
            for message in streams.contexts[1]
            if isinstance(message, ToolResultMessage)
        ]
        assert tool_results[0].content == [TextContent("project readme")]


if __name__ == "__main__":
    test_read_only_tools()
    test_scripted_model_inspects_workspace_through_core()
    print("all ling_agent tool tests passed")

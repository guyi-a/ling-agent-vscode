"""Tests for ling_core tool registration and execution."""

from __future__ import annotations

import asyncio

from ling_ai.types import ImageContent, TextContent, Tool, ToolCall
from ling_core.tools import AgentTool, ToolContext, ToolExecution, ToolRegistry


def _call(name: str = "echo", value: str = "hello") -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments={"value": value})


def test_tool_registry_contracts() -> None:
    sync_tool = AgentTool(
        definition=Tool(name="echo", description="Echo."),
        handler=lambda call, context: call.arguments["value"],
    )
    registry = ToolRegistry([sync_tool])

    assert registry.get("echo") is sync_tool
    assert [tool.name for tool in registry.definitions()] == ["echo"]

    try:
        registry.register(sync_tool)
    except ValueError as exc:
        assert "Duplicate tool name" in str(exc)
    else:
        raise AssertionError("Expected duplicate-name validation")


async def _execute_tools() -> None:
    async def echo(call: ToolCall, context: ToolContext) -> str:
        assert context.step == 1
        return call.arguments["value"]

    registry = ToolRegistry(
        [
            AgentTool(
                definition=Tool(name="echo", description="Echo."),
                handler=echo,
            ),
            AgentTool(
                definition=Tool(name="image", description="Return an image."),
                handler=lambda call, context: [
                    ImageContent(data="aW1n", mime_type="image/png")
                ],
            ),
            AgentTool(
                definition=Tool(name="empty", description="Return nothing."),
                handler=lambda call, context: None,
            ),
            AgentTool(
                definition=Tool(name="fail", description="Fail."),
                handler=lambda call, context: (_ for _ in ()).throw(
                    ValueError("boom")
                ),
            ),
            AgentTool(
                definition=Tool(name="structured", description="Structured error."),
                handler=lambda call, context: ToolExecution(
                    output="command output",
                    is_error=True,
                ),
            ),
        ]
    )
    context = ToolContext(step=1)

    echoed = await registry.execute(_call(), context)
    image = await registry.execute(_call("image"), context)
    empty = await registry.execute(_call("empty"), context)
    failed = await registry.execute(_call("fail"), context)
    structured = await registry.execute(_call("structured"), context)
    missing = await registry.execute(_call("missing"), context)

    assert echoed.content == [TextContent("hello")]
    assert not echoed.is_error
    assert image.content == [ImageContent(data="aW1n", mime_type="image/png")]
    assert empty.content == [TextContent("(no output)")]
    assert failed.is_error
    assert failed.content[0].text == "Error: ValueError: boom"
    assert structured.is_error
    assert structured.content[0].text == "command output"
    assert missing.is_error
    assert missing.content[0].text == "Error: unknown tool: missing"
    assert all(
        result.tool_call_id == "call_1"
        for result in (echoed, image, empty, failed, structured, missing)
    )


def test_tool_execution() -> None:
    asyncio.run(_execute_tools())


if __name__ == "__main__":
    test_tool_registry_contracts()
    test_tool_execution()
    print("all ling_core tool tests passed")

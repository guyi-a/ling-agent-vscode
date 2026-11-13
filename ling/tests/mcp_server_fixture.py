"""Small stdio MCP server used by integration tests."""

from __future__ import annotations

import sys

from mcp import types
from mcp.server.fastmcp import FastMCP

_transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
_port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
mcp = FastMCP("Ling MCP fixture", port=_port)


@mcp.tool(
    description="Echo text back to the caller.",
    annotations=types.ToolAnnotations(readOnlyHint=True),
)
def echo(text: str) -> str:
    return f"echo:{text}"


@mcp.tool(description="Add two integers.")
def add(left: int, right: int) -> int:
    return left + right


if __name__ == "__main__":
    mcp.run(transport=_transport)

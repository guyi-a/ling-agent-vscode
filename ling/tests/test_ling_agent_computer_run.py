"""Tests that computer control ends with the agent run."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from ling_ai.types import TextContent
from ling_agent import CodingAgent

from .test_ling_agent_integration import MODEL, ScriptedStreams, _assistant


class FakeComputerService:
    def __init__(self) -> None:
        self.stops: list[str] = []
        self.session_id = ""

    def bind_session(self, session_id: str) -> None:
        self.session_id = session_id

    def deny_reason(self, effect: object) -> None:
        return None

    def is_covered(self, effect: object) -> bool:
        return False

    def note_approval_granted(
        self,
        effect: object,
        approval_id: str = "",
    ) -> None:
        return None

    async def stop(self, reason: str = "user", **_: Any) -> bool:
        self.stops.append(reason)
        return True


async def _run(root: Path) -> None:
    service = FakeComputerService()
    agent = CodingAgent(
        model=MODEL,
        stream_fn=ScriptedStreams(
            [_assistant([TextContent("done")])]
        ),
        workspace_root=root,
        computer_service=service,
    )
    try:
        result = await agent.prompt("hi")
    finally:
        await agent.close()
    assert result.content == [TextContent("done")]
    assert service.session_id
    assert service.stops == ["run_finished"]


def test_run_finished_stops_computer_control() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_run(Path(temp)))


if __name__ == "__main__":
    test_run_finished_stops_computer_control()
    print("computer run tests passed")

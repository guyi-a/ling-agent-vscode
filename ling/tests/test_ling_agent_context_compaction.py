"""Integration tests for model-view context compaction."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    Usage,
    UserMessage,
)

from ling_agent import CodingAgent, CompactionApplied, HarnessConfig
from ling_agent.compaction import CompactionConfig, CompactionRow

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason="stop",
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


def _compaction_config() -> CompactionConfig:
    return CompactionConfig(
        window_nominal_tokens=1_000,
        window_usable_ratio=1.0,
        reserved_output_tokens=0,
        buffer_tokens=0,
        keep_last_user_turns=1,
        summarizer_timeout_seconds=5.0,
    )


def test_coding_agent_compacts_model_view_only() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        streams = ScriptedStreams(
            [
                _assistant("a" * 10_000),
                _assistant("done"),
                _assistant("done3"),
            ]
        )
        summarized: list[list[str]] = []

        async def summarize(
            folded: list[CompactionRow],
            prior: str,
        ) -> str:
            summarized.append([row.id for row in folded])
            return "## 1. User Intent\nsummary"

        session_id: str | None = None

        async def run() -> None:
            nonlocal session_id
            agent = CodingAgent(
                model=MODEL,
                stream_fn=streams,
                workspace_root=workspace,
                storage_root=root / "state",
                config=HarnessConfig(
                    compaction=_compaction_config(),
                ),
                summarize_fn=summarize,
            )
            try:
                await agent.prompt("u" * 10_000)
                await agent.prompt("second")
                await agent.prompt("third")
                session = agent.session
                assert session is not None
                session_id = session.session_id
                assert session.active_compaction is not None
                assert len(session.context.messages) == 6
                events = await agent.store.load_committed(
                    session.session_id
                )
                assert any(
                    isinstance(envelope.event, CompactionApplied)
                    for envelope in events
                )
            finally:
                await agent.close()

        asyncio.run(run())

        assert summarized == [["user:0", "assistant:1"]]
        assert len(streams.contexts) == 3
        second_view = streams.contexts[1]
        assert len(second_view) == 2
        assert isinstance(second_view[0], UserMessage)
        assert "compacted-summary" in second_view[0].content
        assert isinstance(second_view[1], UserMessage)
        assert second_view[1].content == "second"

        third_view = streams.contexts[2]
        assert len(third_view) == 4
        assert isinstance(third_view[0], UserMessage)
        assert "compacted-summary" in third_view[0].content
        assert isinstance(third_view[-1], UserMessage)
        assert third_view[-1].content == "third"

        async def resume() -> None:
            assert session_id is not None
            resumed = CodingAgent(
                model=MODEL,
                stream_fn=ScriptedStreams([]),
                workspace_root=workspace,
                storage_root=root / "state",
                config=HarnessConfig(
                    compaction=_compaction_config(),
                ),
            )
            try:
                await resumed.resume(session_id)
                assert resumed.session is not None
                assert resumed.session.active_compaction is not None
            finally:
                await resumed.close()

        asyncio.run(resume())

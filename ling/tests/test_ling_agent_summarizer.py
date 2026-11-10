"""Tests for the default compaction summarizer stream."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.events import DoneEvent, ErrorEvent, StartEvent
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
from ling_agent.compaction import CompactionConfig

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)
SUMMARY_MARKER = "CRITICAL: Respond with TEXT ONLY"


def _assistant(
    text: str,
    *,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)] if text else [],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
    )


def _stream_for(message: AssistantMessage) -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()
    stream.push(StartEvent(partial=message))
    if message.stop_reason == "error":
        stream.push(ErrorEvent(reason="error", error=message))
    else:
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
    stream.end()
    return stream


class ScriptedStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)
        self.contexts: list[list[object]] = []
        self.system_prompts: list[str | None] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        self.contexts.append(list(context.messages))
        self.system_prompts.append(context.system_prompt)
        return _stream_for(self.messages.pop(0))


class TimeoutStreams:
    def __init__(self) -> None:
        self.messages = [_assistant("a" * 10_000), _assistant("done")]
        self.contexts: list[list[object]] = []
        self.system_prompts: list[str | None] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        self.contexts.append(list(context.messages))
        self.system_prompts.append(context.system_prompt)
        if context.system_prompt and SUMMARY_MARKER in context.system_prompt:
            return slow_stream()
        return _stream_for(self.messages.pop(0))


def slow_stream() -> AssistantMessageEventStream:
    stream = AssistantMessageEventStream()

    async def produce() -> None:
        await asyncio.sleep(1)
        message = _assistant("late summary")
        stream.push(DoneEvent(reason="stop", message=message))
        stream.end()

    stream.attach_task(asyncio.create_task(produce()))
    return stream


def _config(timeout: float = 5.0) -> CompactionConfig:
    return CompactionConfig(
        window_nominal_tokens=1_000,
        window_usable_ratio=1.0,
        reserved_output_tokens=0,
        buffer_tokens=0,
        keep_last_user_turns=1,
        summarizer_timeout_seconds=timeout,
    )


async def _run_agent(
    root: Path,
    streams: object,
    *,
    timeout: float = 5.0,
) -> CodingAgent:
    workspace = root / "workspace"
    workspace.mkdir()
    agent = CodingAgent(
        model=MODEL,
        stream_fn=streams,  # type: ignore[arg-type]
        workspace_root=workspace,
        storage_root=root / "state",
        config=HarnessConfig(compaction=_config(timeout)),
    )
    await agent.prompt("u" * 10_000)
    await agent.prompt("second")
    return agent


async def _compaction_events(agent: CodingAgent) -> list[object]:
    if agent.session is None:
        return []
    events = await agent.store.load_committed(
        agent.session.session_id
    )
    return [
        envelope.event
        for envelope in events
        if isinstance(envelope.event, CompactionApplied)
    ]


def test_default_summarizer_success() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        streams = ScriptedStreams(
            [
                _assistant("a" * 10_000),
                _assistant("## 1. User Intent\nsummary"),
                _assistant("done"),
            ]
        )

        async def run() -> None:
            agent = await _run_agent(root, streams)
            try:
                assert len(streams.contexts) == 3
                assert streams.system_prompts[1] is not None
                assert SUMMARY_MARKER in streams.system_prompts[1]
                assert any(
                    "<compact-control>" in _message_text(message)
                    for message in streams.contexts[1]
                )
                second_view = streams.contexts[2]
                assert len(second_view) == 2
                assert isinstance(second_view[0], UserMessage)
                assert "## 1. User Intent" in second_view[0].content
                assert isinstance(second_view[1], UserMessage)
                assert second_view[1].content == "second"
                assert await _compaction_events(agent)
            finally:
                await agent.close()

        asyncio.run(run())


def test_empty_summary_keeps_full_context() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        streams = ScriptedStreams(
            [
                _assistant("a" * 10_000),
                _assistant(""),
                _assistant("done"),
            ]
        )

        async def run() -> None:
            agent = await _run_agent(root, streams)
            try:
                assert agent.session is not None
                assert agent.session.active_compaction is None
                assert not await _compaction_events(agent)
                second_view = streams.contexts[2]
                assert len(second_view) == 3
            finally:
                await agent.close()

        asyncio.run(run())


def test_error_summary_keeps_full_context() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        streams = ScriptedStreams(
            [
                _assistant("a" * 10_000),
                _assistant("", stop_reason="error"),
                _assistant("done"),
            ]
        )

        async def run() -> None:
            agent = await _run_agent(root, streams)
            try:
                assert agent.session is not None
                assert agent.session.active_compaction is None
                assert not await _compaction_events(agent)
                assert len(streams.contexts[2]) == 3
            finally:
                await agent.close()

        asyncio.run(run())


def test_summary_timeout_keeps_full_context() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        streams = TimeoutStreams()

        async def run() -> None:
            agent = await _run_agent(
                root,
                streams,
                timeout=0.01,
            )
            try:
                assert agent.session is not None
                assert agent.session.active_compaction is None
                assert not await _compaction_events(agent)
                assert len(streams.contexts[2]) == 3
            finally:
                await agent.close()

        asyncio.run(run())


def _message_text(message: object) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "\n".join(
        block.text
        for block in content
        if isinstance(block, TextContent)
    )

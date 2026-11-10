"""Tests for user-quoted selection context."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from ling_agent import (
    CodingAgent,
    SQLiteSessionStore,
    TimelineProjector,
    UserMessageCommitted,
    UserSelection,
    default_database_path,
)
from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    Usage,
)
from ling_rpc.server import JsonRpcError, _parse_user_selections

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
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


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason="stop",
        timestamp=1,
    )


def test_user_selection_validation() -> None:
    with pytest.raises(ValueError, match="kind"):
        UserSelection(kind="other", label="x", text="x", line_count=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="label"):
        UserSelection(kind="editor", label="", text="x", line_count=1)
    with pytest.raises(ValueError, match="text"):
        UserSelection(kind="editor", label="x", text="  ", line_count=1)
    with pytest.raises(ValueError, match="line_count"):
        UserSelection(kind="editor", label="x", text="x", line_count=0)


def test_parse_user_selections() -> None:
    parsed = _parse_user_selections(
        [
            {
                "kind": "editor",
                "label": "files.py",
                "text": "print('hi')",
                "lineCount": 1,
                "path": "E:\\workspace\\files.py",
                "startLine": 10,
                "endLine": 10,
            }
        ]
    )
    assert parsed[0].kind == "editor"
    assert parsed[0].start_line == 10

    with pytest.raises(JsonRpcError, match="at most"):
        _parse_user_selections(
            [
                {
                    "kind": "chat",
                    "label": "Chat",
                    "text": "x",
                    "lineCount": 1,
                }
                for _ in range(5)
            ]
        )
    with pytest.raises(JsonRpcError, match="kind"):
        _parse_user_selections(
            [{"kind": "bad", "label": "x", "text": "x", "lineCount": 1}]
        )


def test_prompt_injects_selection_and_persists_metadata() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            streams = ScriptedStreams([_assistant("It prints.")])
            agent = CodingAgent(
                model=MODEL,
                stream_fn=streams,
                workspace_root=workspace,
                storage_root=state,
            )
            selection = UserSelection(
                kind="editor",
                label="files.py",
                text="print('hi')",
                line_count=1,
                path=str(workspace / "files.py"),
                start_line=10,
                end_line=10,
            )
            try:
                result = await agent.prompt(
                    "What does this do?",
                    selections=(selection,),
                )
                assert result.stop_reason == "stop"

                user_message = streams.contexts[0][0]
                content = "".join(
                    block.text
                    for block in user_message.content  # type: ignore[union-attr]
                    if isinstance(block, TextContent)
                )
                assert "Selected context" in content
                assert "files.py (lines 10-10, 1 line)" in content
                assert "print('hi')" in content

                timeline = await agent.timeline_snapshot()
                assert timeline is not None
                user_item = timeline.items[0]
                assert getattr(user_item, "text") == "What does this do?"
                assert getattr(user_item, "selections")[0].label == "files.py"

                assert agent.session is not None
                session_id = agent.session.session_id
            finally:
                await agent.close()

            store = SQLiteSessionStore(default_database_path(state))
            try:
                events = await store.load_committed(session_id)
                projector = TimelineProjector(session_id)
                for envelope in events:
                    projector.push_committed(envelope)
                committed = next(
                    envelope.event
                    for envelope in events
                    if isinstance(envelope.event, UserMessageCommitted)
                )
                assert committed.selections[0].text == "print('hi')"
                snapshot_item = projector.snapshot().items[0]
                assert getattr(snapshot_item, "selections")[0].line_count == 1
            finally:
                await store.close()

    asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

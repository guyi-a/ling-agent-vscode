"""Tests for user image attachments from RPC to timeline."""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

from ling_ai.events import (
    DoneEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
)
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    Usage,
    UserMessage,
)
from ling_agent import (
    AssistantTimelineItem,
    CodingAgent,
    CommittedEventEnvelope,
    HarnessConfig,
    RunStarted,
    SQLiteSessionStore,
    Session,
    TimelineProjector,
    UserAttachment,
    UserMessageCommitted,
    UserTimelineItem,
    Workspace,
    WorkspaceFileError,
    WorkspacePathError,
)
from ling_agent.attachments import (
    IMAGE_UNSUPPORTED_MESSAGE,
    friendly_provider_error,
)
from ling_rpc.ui_events import snapshot_to_ui

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image"


def test_legacy_attachment_path_upgrades_to_agents() -> None:
    attachment = UserAttachment(
        relative_path=".ling/attachments/pasted-ab12.png",
        name="pasted-ab12.png",
        media_type="image/png",
    )
    assert (
        attachment.relative_path
        == ".agents/attachments/pasted-ab12.png"
    )


def _assistant(
    text: str,
    *,
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(input_tokens=2, output_tokens=2, total_tokens=4),
        stop_reason=stop_reason,
        timestamp=1,
        error_message=error_message,
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
        for content_index, block in enumerate(message.content):
            if isinstance(block, TextContent) and block.text:
                stream.push(
                    TextStartEvent(
                        content_index=content_index,
                        partial=message,
                    )
                )
                stream.push(
                    TextDeltaEvent(
                        content_index=content_index,
                        delta=block.text,
                        partial=message,
                    )
                )
                stream.push(
                    TextEndEvent(
                        content_index=content_index,
                        content=block.text,
                        partial=message,
                    )
                )
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


def test_workspace_read_image() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        target = root / "assets" / "pic.png"
        target.parent.mkdir()
        target.write_bytes(PNG_BYTES)
        workspace = Workspace(root)

        image = workspace.read_image("assets/pic.png")

        assert image.mime_type == "image/png"
        assert base64.b64decode(image.data) == PNG_BYTES

        unsupported = root / "notes.txt"
        unsupported.write_text("text", encoding="utf-8")
        try:
            workspace.read_image("notes.txt")
        except WorkspaceFileError:
            pass
        else:
            raise AssertionError("Expected unsupported image rejection")

        try:
            workspace.read_image("../outside.png")
        except WorkspacePathError:
            pass
        else:
            raise AssertionError("Expected image path escape rejection")

        small_limit = Workspace(
            root,
            HarnessConfig(max_attachment_image_bytes=4),
        )
        try:
            small_limit.read_image("assets/pic.png")
        except WorkspaceFileError:
            pass
        else:
            raise AssertionError("Expected oversized image rejection")


def test_user_attachment_validation() -> None:
    UserAttachment(
        relative_path=".agents/attachments/pasted-ab12.png",
        name="pasted-ab12.png",
        media_type="image/png",
    )

    invalid = [
        ("", "a.png", "image/png"),
        ("../a.png", "a.png", "image/png"),
        ("C:/a.png", "a.png", "image/png"),
        ("a\\b.png", "a.png", "image/png"),
        ("a.png", "", "image/png"),
        ("a.png", "a.png", "text/plain"),
    ]
    for relative_path, name, media_type in invalid:
        try:
            UserAttachment(
                relative_path=relative_path,
                name=name,
                media_type=media_type,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"Expected invalid attachment rejection: {relative_path!r}"
            )

    UserAttachment(
        relative_path="doc.pdf",
        name="doc.pdf",
        media_type="application/pdf",
        kind="file",
    )
    UserAttachment(
        relative_path=str(Path.cwd() / "doc.pdf"),
        name="doc.pdf",
        media_type="application/pdf",
        kind="file",
    )
    try:
        UserAttachment(
            relative_path="doc.pdf",
            name="doc.pdf",
            media_type="application/pdf",
            kind="other",  # type: ignore[arg-type]
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid attachment kind rejection")


def test_friendly_provider_error() -> None:
    assert friendly_provider_error(None) is None
    assert (
        friendly_provider_error("Connection reset by peer")
        == "Connection reset by peer"
    )
    for message in (
        "Invalid content type: image_url is not supported",
        "This model does not support vision input",
        "unknown variant `image_url`, expected `text`",
    ):
        assert friendly_provider_error(message) == IMAGE_UNSUPPORTED_MESSAGE


async def _coding_agent_maps_image_unsupported_error() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "image.png").write_bytes(PNG_BYTES)
        streams = ScriptedStreams(
            [
                _assistant(
                    "",
                    stop_reason="error",
                    error_message=(
                        "Invalid content type: image_url is not supported"
                    ),
                )
            ]
        )
        agent = CodingAgent(
            model=MODEL,
            stream_fn=streams,
            workspace_root=root,
            storage_root=root / "state",
        )
        attachment = UserAttachment(
            relative_path="image.png",
            name="image.png",
            media_type="image/png",
        )

        try:
            result = await agent.prompt(
                "describe this",
                attachments=[attachment],
            )

            assert result.stop_reason == "error"
            assert result.error_message == IMAGE_UNSUPPORTED_MESSAGE

            snapshot = await agent.timeline_snapshot()
            assert snapshot is not None
            assistant = next(
                item
                for item in snapshot.items
                if isinstance(item, AssistantTimelineItem)
            )
            assert assistant.error == IMAGE_UNSUPPORTED_MESSAGE
        finally:
            await agent.close()


async def _coding_agent_prompt_with_file() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
        streams = ScriptedStreams([_assistant("ok")])
        agent = CodingAgent(
            model=MODEL,
            stream_fn=streams,
            workspace_root=root,
            storage_root=root / "state",
        )
        attachment = UserAttachment(
            relative_path="doc.pdf",
            name="doc.pdf",
            media_type="application/pdf",
            kind="file",
        )

        try:
            await agent.prompt(
                "summarize this",
                attachments=[attachment],
            )

            self_message = streams.contexts[0][0]
            assert isinstance(self_message, UserMessage)
            assert isinstance(self_message.content, list)
            assert len(self_message.content) == 1
            block = self_message.content[0]
            assert isinstance(block, TextContent)
            assert "summarize this" in block.text
            assert "Attached files" in block.text
            assert "doc.pdf" in block.text
            assert "application/pdf" in block.text

            snapshot = await agent.timeline_snapshot()
            assert snapshot is not None
            user_item = next(
                item
                for item in snapshot.items
                if isinstance(item, UserTimelineItem)
            )
            assert user_item.attachments[0].kind == "file"
            assert user_item.text == "summarize this"
        finally:
            await agent.close()


async def _coding_agent_prompt_with_external_file() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        root = base / "workspace"
        root.mkdir()
        external = base / "deck.pptx"
        external.write_bytes(b"PK\x03\x04fake")
        streams = ScriptedStreams([_assistant("ok")])
        agent = CodingAgent(
            model=MODEL,
            stream_fn=streams,
            workspace_root=root,
            storage_root=root / "state",
        )
        attachment = UserAttachment(
            relative_path=str(external),
            name="deck.pptx",
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            kind="file",
        )

        try:
            await agent.prompt(
                "where is the file?",
                attachments=[attachment],
            )

            self_message = streams.contexts[0][0]
            assert isinstance(self_message.content, list)
            block = self_message.content[0]
            assert isinstance(block, TextContent)
            assert str(external) in block.text
            assert not (root / ".agents" / "attachments").exists()

            snapshot = await agent.timeline_snapshot()
            assert snapshot is not None
            user_item = next(
                item
                for item in snapshot.items
                if isinstance(item, UserTimelineItem)
            )
            assert user_item.text == "where is the file?"
            assert user_item.attachments[0].relative_path == str(external)
        finally:
            await agent.close()


async def _coding_agent_prompt_with_image() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "image.png").write_bytes(PNG_BYTES)
        streams = ScriptedStreams([_assistant("I can see it.")])
        agent = CodingAgent(
            model=MODEL,
            stream_fn=streams,
            workspace_root=root,
            storage_root=root / "state",
        )
        attachment = UserAttachment(
            relative_path="image.png",
            name="image.png",
            media_type="image/png",
        )

        try:
            result = await agent.prompt(
                "describe this",
                attachments=[attachment],
            )

            self_message = streams.contexts[0][0]
            assert isinstance(self_message, UserMessage)
            assert isinstance(self_message.content, list)
            assert isinstance(self_message.content[0], ImageContent)
            assert self_message.content[0].mime_type == "image/png"
            assert isinstance(self_message.content[1], TextContent)
            assert self_message.content[1].text == "describe this"
            assert result.content[0].text == "I can see it."

            snapshot = await agent.timeline_snapshot()
            assert snapshot is not None
            user_item = next(
                item
                for item in snapshot.items
                if isinstance(item, UserTimelineItem)
            )
            assert user_item.attachments == (attachment,)

            events = await agent.store.load_committed(snapshot.session_id)
            user_event = next(
                envelope.event
                for envelope in events
                if isinstance(envelope.event, UserMessageCommitted)
            )
            assert user_event.attachments == (attachment,)
        finally:
            await agent.close()


async def _coding_agent_accepts_image_only_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "image.png").write_bytes(PNG_BYTES)
        streams = ScriptedStreams([_assistant("A screenshot.")])
        agent = CodingAgent(
            model=MODEL,
            stream_fn=streams,
            workspace_root=root,
            storage_root=root / "state",
        )
        attachment = UserAttachment(
            relative_path="image.png",
            name="image.png",
            media_type="image/png",
        )

        try:
            await agent.prompt("", attachments=[attachment])

            self_message = streams.contexts[0][0]
            assert isinstance(self_message, UserMessage)
            assert isinstance(self_message.content, list)
            assert len(self_message.content) == 1
            assert isinstance(self_message.content[0], ImageContent)
        finally:
            await agent.close()


async def _coding_agent_rejects_empty_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        agent = CodingAgent(
            model=MODEL,
            stream_fn=ScriptedStreams([]),
            workspace_root=root,
            storage_root=root / "state",
        )
        try:
            try:
                await agent.prompt("")
            except ValueError:
                pass
            else:
                raise AssertionError("Expected empty prompt rejection")
        finally:
            await agent.close()


async def _attachment_event_storage_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        database = root / "sessions.db"
        store = SQLiteSessionStore(database)
        session = await Session.create(
            store,
            workspace_root=root,
            title="attachments",
            context=Context(),
        )
        attachment = UserAttachment(
            relative_path=".agents/attachments/pasted-ab12.png",
            name="pasted-ab12.png",
            media_type="image/png",
        )
        await session.start_run(
            UserMessage(
                content=[
                    ImageContent(data="ZmFrZQ==", mime_type="image/png"),
                    TextContent("what is this?"),
                ],
                timestamp=1,
            ),
            attachments=(attachment,),
            display_text="what is this?",
        )
        session_id = session.session_id
        await store.close()

        reopened = SQLiteSessionStore(database)
        try:
            events = await reopened.load_committed(session_id)
            user_event = next(
                envelope.event
                for envelope in events
                if isinstance(envelope.event, UserMessageCommitted)
            )
            assert user_event.attachments == (attachment,)
            assert user_event.display_text == "what is this?"
            content = user_event.message.content
            assert isinstance(content, list)
            assert isinstance(content[0], ImageContent)
            assert content[0].data == "ZmFrZQ=="
        finally:
            await reopened.close()


def test_snapshot_to_ui_includes_attachments() -> None:
    attachment = UserAttachment(
        relative_path=".agents/attachments/pasted-ab12.png",
        name="pasted-ab12.png",
        media_type="image/png",
    )
    projector = TimelineProjector("session-1")
    projector.push_committed(
        CommittedEventEnvelope(
            session_id="session-1",
            seq=1,
            event_id="event-1",
            run_id="run-1",
            turn_id="turn-1",
            step=1,
            event=RunStarted(),
        )
    )
    projector.push_committed(
        CommittedEventEnvelope(
            session_id="session-1",
            seq=2,
            event_id="event-2",
            run_id="run-1",
            turn_id="turn-1",
            step=1,
            event=UserMessageCommitted(
                message=UserMessage(
                    content=[
                        ImageContent(
                            data="ZmFrZQ==",
                            mime_type="image/png",
                        )
                    ],
                    timestamp=1,
                ),
                attachments=(attachment,),
            ),
            message_id="message-user-1",
        )
    )

    snapshot = snapshot_to_ui(projector.snapshot())
    user_entry = next(
        entry
        for entry in snapshot["entries"]
        if entry.get("kind") == "message"
        and entry["message"]["role"] == "user"
    )

    assert user_entry["message"]["attachments"] == [
        {
            "relativePath": ".agents/attachments/pasted-ab12.png",
            "name": "pasted-ab12.png",
            "mediaType": "image/png",
            "kind": "image",
        }
    ]


def test_coding_agent_prompt_with_external_file() -> None:
    asyncio.run(_coding_agent_prompt_with_external_file())


def test_coding_agent_prompt_with_file() -> None:
    asyncio.run(_coding_agent_prompt_with_file())


def test_coding_agent_maps_image_unsupported_error() -> None:
    asyncio.run(_coding_agent_maps_image_unsupported_error())


def test_coding_agent_prompt_with_image() -> None:
    asyncio.run(_coding_agent_prompt_with_image())


def test_coding_agent_accepts_image_only_prompt() -> None:
    asyncio.run(_coding_agent_accepts_image_only_prompt())


def test_coding_agent_rejects_empty_prompt() -> None:
    asyncio.run(_coding_agent_rejects_empty_prompt())


def test_attachment_event_storage_round_trip() -> None:
    asyncio.run(_attachment_event_storage_round_trip())

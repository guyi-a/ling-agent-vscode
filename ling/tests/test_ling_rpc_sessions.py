"""End-to-end tests for RPC session events and approval digests."""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from ling_ai.events import (
    DoneEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantContent,
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ToolCall,
    Usage,
)

from ling_agent import (
    ApprovalKind,
    ApprovalMode,
    ApprovalRequest,
    ApprovalRequested,
    CommittedEventEnvelope,
    HarnessConfig,
    RunStarted,
    RunStopped,
    SessionRecord,
    SQLiteSessionStore,
    UserAttachment,
    UserSelection,
    default_database_path,
)
from ling_agent.attachments import IMAGE_UNSUPPORTED_MESSAGE
from ling_agent.compaction import CompactionConfig
from ling_rpc.sessions import SessionService
from ling_rpc.server import JsonRpcServer

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _assistant(
    content: list[AssistantContent],
    *,
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
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
                    TextStartEvent(content_index=content_index, partial=message)
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
            elif isinstance(block, ToolCall):
                stream.push(
                    ToolCallStartEvent(
                        content_index=content_index,
                        partial=message,
                    )
                )
                stream.push(
                    ToolCallDeltaEvent(
                        content_index=content_index,
                        delta=json.dumps(block.arguments),
                        partial=message,
                    )
                )
                stream.push(
                    ToolCallEndEvent(
                        content_index=content_index,
                        tool_call=block,
                        partial=message,
                    )
                )
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


async def _seed_pending_approval(state: Path, workspace: Path) -> None:
    store = SQLiteSessionStore(default_database_path(state))
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-interrupted",
                workspace_root=workspace,
                title="interrupted",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        request = ApprovalRequest(
            kind=ApprovalKind.WRITE,
            tool_name="write_file",
            arguments={"path": "a.txt"},
            reason="write",
            workspace_root=workspace,
            call_id="call-1",
            approval_id="approval-1",
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-interrupted",
                seq=1,
                event_id="event-1",
                run_id="run-1",
                turn_id="turn-1",
                step=0,
                event=RunStarted(),
            )
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-interrupted",
                seq=2,
                event_id="event-2",
                run_id="run-1",
                turn_id="turn-1",
                step=0,
                event=ApprovalRequested(
                    approval_id="approval-1",
                    request=request,
                ),
                call_id="call-1",
                approval_id="approval-1",
            )
        )
    finally:
        await store.close()


async def _wait_for(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    observed: Callable[[], object] | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    detail = observed() if observed is not None else None
    raise AssertionError(f"Timed out waiting for RPC UI event: {detail!r}")


class LingRpcSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_events_and_approval_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="write-1",
                                name="write_file",
                                arguments={
                                    "path": "created.txt",
                                    "content": "hello",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("File created.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                accepted = await service.start(
                    "Create created.txt",
                    str(workspace),
                )
                self.assertEqual(accepted, {"accepted": True})

                await _wait_for(
                    lambda: any(
                        event.get("type") == "session.snapshot"
                        and event["snapshot"].get("pendingApprovals")
                        for event in events
                    ),
                    observed=lambda: events,
                )
                pending_snapshot = next(
                    event
                    for event in reversed(events)
                    if event.get("type") == "session.snapshot"
                    and event["snapshot"].get("pendingApprovals")
                )
                approval = pending_snapshot["snapshot"]["pendingApprovals"][0]
                self.assertTrue(approval["effectDigest"])
                self.assertEqual(approval["target"], "created.txt")
                self.assertEqual(
                    approval["arguments"]["path"],
                    "created.txt",
                )

                rejected = await service.resolve_approval(
                    approval["id"],
                    "wrong-digest",
                    True,
                )
                self.assertFalse(rejected)
                resolved = await service.resolve_approval(
                    approval["id"],
                    approval["effectDigest"],
                    True,
                )
                self.assertTrue(resolved)

                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
                sessions = await service.list_sessions()
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]["title"], "Create created.txt")
                history = await service.history_snapshot(sessions[0]["id"])
                self.assertTrue(history["readOnly"])
                self.assertTrue(
                    any(
                        entry.get("kind") == "message"
                        and entry["message"]["role"] == "user"
                        for entry in history["entries"]
                    )
                )
                self.assertTrue(
                    any(
                        entry.get("kind") == "tool"
                        for entry in history["entries"]
                    )
                )
            finally:
                await service.close()

            self.assertEqual(
                (workspace / "created.txt").read_text(encoding="utf-8"),
                "hello",
            )
            self.assertTrue(
                any(
                    event.get("type") == "message.delta"
                    and event.get("delta") == "File created."
                    for event in events
                )
            )
            latest = next(
                event
                for event in reversed(events)
                if event.get("type") == "session.snapshot"
            )
            self.assertEqual(latest["snapshot"]["pendingApprovals"], [])
            self.assertEqual(latest["snapshot"]["phase"], "idle")
            self.assertTrue(
                any(
                    entry.get("kind") == "approval"
                    and entry["approval"]["approved"] is True
                    for entry in latest["snapshot"]["entries"]
                )
            )
            tools = [
                entry["tool"]
                for entry in latest["snapshot"]["entries"]
                if entry.get("kind") == "tool"
            ]
            self.assertEqual(tools[0]["status"], "Completed")
            self.assertTrue(
                any(
                    entry.get("kind") == "message"
                    and entry["message"]["role"] == "assistant"
                    and entry["message"]["content"] == "File created."
                    for entry in latest["snapshot"]["entries"]
                )
            )

    async def test_history_can_resume_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant([TextContent("First reply.")]),
                    _assistant([TextContent("Second reply.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                await service.start("First question", str(workspace))
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
                sessions = await service.list_sessions()
                session_id = sessions[0]["id"]

                opened = await service.open_session(session_id)
                self.assertFalse(opened["readOnly"])
                self.assertEqual(opened["sessionId"], session_id)

                await service.start("Second question", str(workspace))
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
                sessions_after = await service.list_sessions()
                self.assertEqual(len(sessions_after), 1)
                self.assertEqual(sessions_after[0]["id"], session_id)
                latest_snapshot = next(
                    event
                    for event in reversed(events)
                    if event.get("type") == "session.snapshot"
                )
                user_contents = [
                    entry["message"]["content"]
                    for entry in latest_snapshot["snapshot"]["entries"]
                    if entry.get("kind") == "message"
                    and entry["message"]["role"] == "user"
                ]
                self.assertEqual(
                    user_contents,
                    ["First question", "Second question"],
                )

                await service.new_session()
                reset_snapshot = events[-1]
                self.assertEqual(reset_snapshot["type"], "session.snapshot")
                self.assertEqual(reset_snapshot["snapshot"]["phase"], "idle")
                self.assertEqual(reset_snapshot["snapshot"]["entries"], [])
            finally:
                await service.close()

            self.assertEqual(len(streams.contexts), 2)
            resumed_roles = [
                getattr(message, "role", None)
                for message in streams.contexts[1]
            ]
            self.assertEqual(
                resumed_roles,
                ["user", "assistant", "user"],
            )
            self.assertEqual(
                getattr(streams.contexts[1][0], "content", None),
                "First question",
            )
            self.assertEqual(
                getattr(streams.contexts[1][2], "content", None),
                "Second question",
            )

    async def test_approval_mode_applies_to_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="auto-write",
                                name="write_file",
                                arguments={
                                    "path": "auto.txt",
                                    "content": "auto",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("Written.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )

            try:
                applied = await service.set_approval_mode(ApprovalMode.AUTO)
                self.assertEqual(applied, ApprovalMode.AUTO)
                await service.start(
                    "Write auto.txt",
                    str(workspace),
                    approval_mode=ApprovalMode.AUTO,
                )
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)

                self.assertFalse(
                    any(
                        event.get("type") == "session.snapshot"
                        and event["snapshot"].get("pendingApprovals")
                        for event in events
                    )
                )
                latest = next(
                    event
                    for event in reversed(events)
                    if event.get("type") == "session.snapshot"
                )
                self.assertEqual(latest["snapshot"]["approvalMode"], "auto")

                sessions = await service.list_sessions()
                history = await service.history_snapshot(sessions[0]["id"])
                self.assertEqual(history["approvalMode"], "auto")

                await service.new_session()
                reset_snapshot = events[-1]
                self.assertEqual(
                    reset_snapshot["snapshot"]["approvalMode"],
                    "manual",
                )
            finally:
                await service.close()

            self.assertEqual(
                (workspace / "auto.txt").read_text(encoding="utf-8"),
                "auto",
            )

    async def test_snapshot_preserves_pending_while_sidecar_alive(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="live-write",
                                name="write_file",
                                arguments={
                                    "path": "live.txt",
                                    "content": "live",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("Written.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )

            try:
                await service.start("Write live.txt", str(workspace))
                await _wait_for(
                    lambda: any(
                        event.get("type") == "session.snapshot"
                        and event["snapshot"].get("pendingApprovals")
                        for event in events
                    ),
                    observed=lambda: events,
                )

                snapshot = await service.snapshot()
                self.assertEqual(
                    len(snapshot["pendingApprovals"]),
                    1,
                )
                approval = snapshot["pendingApprovals"][0]
                resolved = await service.resolve_approval(
                    approval["id"],
                    approval["effectDigest"],
                    True,
                )
                self.assertTrue(resolved)
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
            finally:
                await service.close()

            self.assertEqual(
                (workspace / "live.txt").read_text(encoding="utf-8"),
                "live",
            )

    async def test_parallel_tools_queue_multiple_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="write-a",
                                name="write_file",
                                arguments={
                                    "path": "a.txt",
                                    "content": "a",
                                },
                            ),
                            ToolCall(
                                id="write-b",
                                name="write_file",
                                arguments={
                                    "path": "b.txt",
                                    "content": "b",
                                },
                            ),
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("Both written.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )

            try:
                await service.start(
                    "Write two files",
                    str(workspace),
                )
                await _wait_for(
                    lambda: any(
                        event.get("type") == "session.snapshot"
                        and len(
                            event["snapshot"].get(
                                "pendingApprovals"
                            )
                            or []
                        )
                        >= 2
                        for event in events
                    ),
                    observed=lambda: events,
                )

                snapshot = await service.snapshot()
                pending = snapshot["pendingApprovals"]
                self.assertEqual(len(pending), 2)
                for approval in pending:
                    resolved = await service.resolve_approval(
                        approval["id"],
                        approval["effectDigest"],
                        True,
                    )
                    self.assertTrue(resolved)
                await asyncio.wait_for(
                    service.wait_for_idle(),
                    timeout=3,
                )
            finally:
                await service.close()

            self.assertEqual(
                (workspace / "a.txt").read_text(encoding="utf-8"),
                "a",
            )
            self.assertEqual(
                (workspace / "b.txt").read_text(encoding="utf-8"),
                "b",
            )
            latest = next(
                event
                for event in reversed(events)
                if event.get("type") == "session.snapshot"
            )
            self.assertEqual(
                latest["snapshot"]["pendingApprovals"],
                [],
            )
            self.assertEqual(
                len(
                    [
                        entry
                        for entry in latest["snapshot"]["entries"]
                        if entry.get("kind") == "approval"
                    ]
                ),
                2,
            )

    async def test_compaction_marker_in_rpc_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            first_response = _assistant(
                [TextContent("a" * 10_000)]
            )
            first_response.usage.total_tokens = 5_000
            streams = ScriptedStreams(
                [
                    first_response,
                    _assistant([TextContent("done")]),
                ]
            )

            async def summarize(
                folded: object,
                prior: str,
            ) -> str:
                return "## 1. User Intent\nsummary"

            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
                config=HarnessConfig(
                    compaction=CompactionConfig(
                        window_nominal_tokens=1_000,
                        window_usable_ratio=1.0,
                        reserved_output_tokens=0,
                        buffer_tokens=0,
                        keep_last_user_turns=1,
                        summarizer_timeout_seconds=5.0,
                    )
                ),
                summarize_fn=summarize,
            )

            try:
                await service.start("u" * 10_000, str(workspace))
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
                await service.start("second", str(workspace))
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
            finally:
                await service.close()

            compaction_entries = [
                entry
                for event in events
                if event.get("type") == "session.snapshot"
                for entry in event["snapshot"].get("entries", [])
                if entry.get("kind") == "compaction"
            ]
            self.assertTrue(compaction_entries)
            self.assertEqual(
                compaction_entries[-1]["compaction"]["replacedCount"],
                2,
            )

    async def test_restart_recovers_pending_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            await _seed_pending_approval(state, workspace)

            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=ScriptedStreams([]),
                storage_root=state,
            )

            try:
                snapshot = await service.snapshot()
                self.assertEqual(snapshot["phase"], "idle")
                sessions = await service.list_sessions()
                self.assertEqual(sessions[0]["status"], "idle")

                history = await service.history_snapshot(
                    "session-interrupted"
                )
                self.assertEqual(history["pendingApprovals"], [])
                approvals = [
                    entry
                    for entry in history["entries"]
                    if entry.get("kind") == "approval"
                ]
                self.assertEqual(len(approvals), 1)
                self.assertFalse(approvals[0]["approval"]["approved"])
            finally:
                await service.close()

            store = SQLiteSessionStore(default_database_path(state))
            try:
                committed = await store.load_committed(
                    "session-interrupted"
                )
            finally:
                await store.close()
            self.assertIsInstance(committed[-1].event, RunStopped)
            self.assertEqual(committed[-1].event.status, "aborted")

    async def test_rpc_set_mode_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=ScriptedStreams([]),
                storage_root=root / "state",
            )
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=service,
            )

            try:
                applied = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "session.setMode",
                        "params": {"mode": "accept-write"},
                    }
                )
                self.assertEqual(
                    applied["result"],
                    {"mode": "accept-write"},
                )

                snapshot = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "session.snapshot",
                        "params": {},
                    }
                )
                self.assertEqual(snapshot["result"]["phase"], "idle")
                self.assertEqual(
                    snapshot["result"]["approvalMode"],
                    "accept-write",
                )

                invalid = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session.setMode",
                        "params": {"mode": "sometimes"},
                    }
                )
                self.assertEqual(invalid["error"]["code"], -32602)

                missing = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session.setMode",
                        "params": {},
                    }
                )
                self.assertEqual(missing["error"]["code"], -32602)

                bad_start = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "session.start",
                        "params": {
                            "prompt": "test",
                            "workspaceRoot": str(workspace),
                            "approvalMode": "sometimes",
                        },
                    }
                )
                self.assertEqual(bad_start["error"]["code"], -32602)
            finally:
                await service.close()

    async def test_rpc_session_start_with_image_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "image.png").write_bytes(
                b"\x89PNG\r\n\x1a\nfake-image"
            )
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [_assistant([TextContent("I can see it.")])]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=service,
            )
            attachment = {
                "relativePath": "image.png",
                "name": "image.png",
                "mediaType": "image/png",
            }

            try:
                response = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "session.start",
                        "params": {
                            "prompt": "",
                            "workspaceRoot": str(workspace),
                            "attachments": [attachment],
                        },
                    }
                )
                self.assertEqual(response["result"], {"accepted": True})

                await asyncio.wait_for(
                    service.wait_for_idle(),
                    timeout=3,
                )
                snapshot = await service.snapshot()
                user_entry = next(
                    entry
                    for entry in snapshot["entries"]
                    if entry.get("kind") == "message"
                    and entry["message"]["role"] == "user"
                )
                self.assertEqual(
                    user_entry["message"]["attachments"],
                    [{**attachment, "kind": "image"}],
                )

                user_message = streams.contexts[0][0]
                self.assertIsInstance(user_message.content, list)
                self.assertIsInstance(
                    user_message.content[0],
                    ImageContent,
                )

                invalid_cases = [
                    {
                        "prompt": "",
                        "workspaceRoot": str(workspace),
                    },
                    {
                        "prompt": "test",
                        "workspaceRoot": str(workspace),
                        "attachments": "bad",
                    },
                    {
                        "prompt": "test",
                        "workspaceRoot": str(workspace),
                        "attachments": [
                            {
                                "relativePath": f"image-{index}.png",
                                "name": f"image-{index}.png",
                                "mediaType": "image/png",
                            }
                            for index in range(5)
                        ],
                    },
                    {
                        "prompt": "test",
                        "workspaceRoot": str(workspace),
                        "attachments": [
                            {
                                "relativePath": "../escape.png",
                                "name": "escape.png",
                                "mediaType": "image/png",
                            }
                        ],
                    },
                    {
                        "prompt": "test",
                        "workspaceRoot": str(workspace),
                        "attachments": [
                            {
                                "relativePath": "image.png",
                                "name": "image.png",
                                "mediaType": "image/png",
                                "kind": "other",
                            }
                        ],
                    },
                ]
                for index, params in enumerate(invalid_cases, start=2):
                    invalid = await server.handle_message(
                        {
                            "jsonrpc": "2.0",
                            "id": index,
                            "method": "session.start",
                            "params": params,
                        }
                    )
                    self.assertEqual(invalid["error"]["code"], -32602)
            finally:
                await service.close()

    async def test_rpc_session_start_with_file_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [_assistant([TextContent("I will read it.")])]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=service,
            )
            attachment = {
                "relativePath": "doc.pdf",
                "name": "doc.pdf",
                "mediaType": "application/pdf",
                "kind": "file",
            }

            try:
                response = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "session.start",
                        "params": {
                            "prompt": "read this",
                            "workspaceRoot": str(workspace),
                            "attachments": [attachment],
                        },
                    }
                )
                self.assertEqual(response["result"], {"accepted": True})

                await asyncio.wait_for(
                    service.wait_for_idle(),
                    timeout=3,
                )
                snapshot = await service.snapshot()
                user_entry = next(
                    entry
                    for entry in snapshot["entries"]
                    if entry.get("kind") == "message"
                    and entry["message"]["role"] == "user"
                )
                self.assertEqual(
                    user_entry["message"]["content"],
                    "read this",
                )
                self.assertEqual(
                    user_entry["message"]["attachments"],
                    [attachment],
                )

                user_message = streams.contexts[0][0]
                self.assertIsInstance(user_message.content, list)
                block = user_message.content[0]
                self.assertIsInstance(block, TextContent)
                self.assertIn("Attached files", block.text)
                self.assertIn("doc.pdf", block.text)
            finally:
                await service.close()

    async def test_rpc_image_error_is_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "image.png").write_bytes(
                b"\x89PNG\r\n\x1a\nfake-image"
            )
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [],
                        stop_reason="error",
                        error_message=(
                            "Invalid content type: image_url is not supported"
                        ),
                    )
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )

            try:
                await service.start(
                    "",
                    str(workspace),
                    attachments=(
                        UserAttachment(
                            relative_path="image.png",
                            name="image.png",
                            media_type="image/png",
                        ),
                    ),
                )
                await asyncio.wait_for(
                    service.wait_for_idle(),
                    timeout=3,
                )

                failed = [
                    event
                    for event in events
                    if event.get("type") == "run.failed"
                ]
                self.assertTrue(failed)
                self.assertEqual(
                    failed[-1]["message"],
                    IMAGE_UNSUPPORTED_MESSAGE,
                )
                snapshot = await service.snapshot()
                self.assertEqual(snapshot["phase"], "failed")
                self.assertEqual(
                    snapshot["error"],
                    IMAGE_UNSUPPORTED_MESSAGE,
                )
            finally:
                await service.close()

    async def test_follow_up_message_is_queued_then_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="write-1",
                                name="write_file",
                                arguments={
                                    "path": "created.txt",
                                    "content": "hello",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("First done.")]),
                    _assistant([TextContent("Second done.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                await service.start("First message", str(workspace))
                await _wait_for(
                    lambda: any(
                        event.get("type") == "session.snapshot"
                        and event["snapshot"].get("pendingApprovals")
                        for event in events
                    ),
                    observed=lambda: events,
                )

                queued = await service.start(
                    "Second message",
                    str(workspace),
                )
                self.assertTrue(queued["queued"])
                self.assertEqual(queued["queueLength"], 1)

                snapshot = await service.snapshot()
                self.assertEqual(len(snapshot["queuedMessages"]), 1)
                self.assertEqual(
                    snapshot["queuedMessages"][0]["text"],
                    "Second message",
                )
                self.assertTrue(
                    any(
                        event.get("type") == "session.queue"
                        and event["queuedMessages"]
                        for event in events
                    )
                )

                pending_snapshot = next(
                    event
                    for event in reversed(events)
                    if event.get("type") == "session.snapshot"
                    and event["snapshot"].get("pendingApprovals")
                )
                approval = pending_snapshot["snapshot"]["pendingApprovals"][0]
                self.assertTrue(
                    await service.resolve_approval(
                        approval["id"],
                        approval["effectDigest"],
                        True,
                    )
                )

                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
                self.assertEqual(len(streams.contexts), 3)
                second_context = streams.contexts[2]
                self.assertTrue(
                    any(
                        getattr(message, "role", None) == "user"
                        and "Second message" in str(
                            getattr(message, "content", "")
                        )
                        for message in second_context
                    )
                )
                final = await service.snapshot()
                self.assertEqual(final["queuedMessages"], [])
                assistant_texts = [
                    entry["message"]["content"]
                    for entry in final["entries"]
                    if entry.get("kind") == "message"
                    and entry["message"]["role"] == "assistant"
                ]
                self.assertIn("Second done.", assistant_texts)
            finally:
                await service.close()

    async def test_send_queued_now_interrupts_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="write-1",
                                name="write_file",
                                arguments={
                                    "path": "created.txt",
                                    "content": "hello",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("Promoted done.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                await service.start("First message", str(workspace))
                await _wait_for(
                    lambda: any(
                        event.get("type") == "session.snapshot"
                        and event["snapshot"].get("pendingApprovals")
                        for event in events
                    ),
                    observed=lambda: events,
                )
                await service.start("Promoted message", str(workspace))
                snapshot = await service.snapshot()
                queued_id = snapshot["queuedMessages"][0]["id"]

                self.assertTrue(
                    await service.send_queued_now(queued_id)
                )
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)

                self.assertTrue(
                    any(
                        event.get("type") == "run.cancelled"
                        for event in events
                    )
                )
                final = await service.snapshot()
                self.assertEqual(final["queuedMessages"], [])
                assistant_texts = [
                    entry["message"]["content"]
                    for entry in final["entries"]
                    if entry.get("kind") == "message"
                    and entry["message"]["role"] == "assistant"
                ]
                self.assertIn("Promoted done.", assistant_texts)
            finally:
                await service.close()

    async def test_cancel_clears_queued_follow_ups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="write-1",
                                name="write_file",
                                arguments={
                                    "path": "created.txt",
                                    "content": "hello",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    )
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                await service.start("First message", str(workspace))
                await _wait_for(
                    lambda: any(
                        event.get("type") == "session.snapshot"
                        and event["snapshot"].get("pendingApprovals")
                        for event in events
                    ),
                    observed=lambda: events,
                )
                await service.start("Queued message", str(workspace))
                await service.cancel()
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)

                snapshot = await service.snapshot()
                self.assertEqual(snapshot["queuedMessages"], [])
            finally:
                await service.close()

    async def test_session_start_with_selection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams([_assistant([TextContent("Got it.")])])
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                accepted = await service.start(
                    "",
                    str(workspace),
                    selections=(
                        UserSelection(
                            kind="chat",
                            label="Assistant",
                            text="This is the quoted answer.",
                            line_count=2,
                        ),
                    ),
                )
                self.assertEqual(accepted, {"accepted": True})
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)

                user_message = streams.contexts[0][0]
                content = "".join(
                    block.text
                    for block in user_message.content
                    if isinstance(block, TextContent)
                )
                self.assertIn("Selected context", content)
                self.assertIn("This is the quoted answer.", content)

                snapshot = await service.snapshot()
                user_entry = next(
                    entry
                    for entry in snapshot["entries"]
                    if entry.get("kind") == "message"
                    and entry["message"]["role"] == "user"
                )
                self.assertEqual(
                    user_entry["message"]["selections"],
                    [
                        {
                            "kind": "chat",
                            "label": "Assistant",
                            "lineCount": 2,
                        }
                    ],
                )
            finally:
                await service.close()

    async def test_ask_user_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="ask-1",
                                name="ask_user",
                                arguments={
                                    "questions": [
                                        {
                                            "question": "PDF style?",
                                            "options": ["Fast", "Pretty"],
                                        }
                                    ]
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("Building it.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                await service.start("Make a PDF", str(workspace))
                await _wait_for(
                    lambda: any(
                        event.get("type") == "question.requested"
                        for event in events
                    ),
                    observed=lambda: events,
                )
                requested = next(
                    event["question"]
                    for event in events
                    if event.get("type") == "question.requested"
                )
                self.assertEqual(requested["callId"], "ask-1")
                self.assertEqual(
                    requested["questions"][0]["options"][0]["label"],
                    "Fast",
                )

                snapshot = await service.snapshot()
                self.assertEqual(snapshot["pendingQuestions"], [requested])

                resolved = await service.resolve_question(
                    "ask-1",
                    [{"id": "q1", "selected": ["Pretty"]}],
                )
                self.assertTrue(resolved)
                self.assertFalse(
                    await service.resolve_question(
                        "ask-1",
                        [{"id": "q1", "selected": ["Pretty"]}],
                    )
                )

                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
                resolution = next(
                    event
                    for event in events
                    if event.get("type") == "question.resolved"
                )
                self.assertFalse(resolution["cancelled"])
                self.assertEqual(
                    resolution["answers"],
                    [{"id": "q1", "selected": ["Pretty"]}],
                )

                tool_result = streams.contexts[1][-1]
                self.assertEqual(tool_result.tool_name, "ask_user")
                self.assertIn("Pretty", tool_result.content[0].text)
            finally:
                await service.close()

    async def test_ask_user_is_cancelled_by_run_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            state = root / "state"
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="ask-1",
                                name="ask_user",
                                arguments={
                                    "questions": [
                                        {
                                            "question": "PDF style?",
                                            "options": ["Fast", "Pretty"],
                                        }
                                    ]
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    )
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=state,
            )

            try:
                await service.start("Make a PDF", str(workspace))
                await _wait_for(
                    lambda: any(
                        event.get("type") == "question.requested"
                        for event in events
                    ),
                    observed=lambda: events,
                )
                await service.cancel()
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)

                resolution = next(
                    event
                    for event in events
                    if event.get("type") == "question.resolved"
                )
                self.assertTrue(resolution["cancelled"])
            finally:
                await service.close()

    async def test_rpc_history_search_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            streams = ScriptedStreams(
                [
                    _assistant([TextContent("Banana is yellow.")]),
                    _assistant([TextContent("Apple is red.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )

            try:
                await service.start(
                    "First question about banana",
                    str(workspace),
                )
                await asyncio.wait_for(
                    service.wait_for_idle(),
                    timeout=3,
                )
                await service.new_session()
                await service.start(
                    "Second question about apple",
                    str(workspace),
                )
                await asyncio.wait_for(
                    service.wait_for_idle(),
                    timeout=3,
                )

                sessions = await service.list_sessions()
                self.assertEqual(len(sessions), 2)

                banana = await service.search_sessions("banana")
                self.assertEqual(len(banana), 1)
                self.assertIn("banana", banana[0]["snippet"].lower())

                red = await service.search_sessions("red")
                self.assertEqual(len(red), 1)
                self.assertIn("red", red[0]["snippet"].lower())

                self.assertEqual(
                    await service.search_sessions("missing"),
                    [],
                )

                first_id = next(
                    session["id"]
                    for session in sessions
                    if "First" in session["title"]
                )
                second_id = next(
                    session["id"]
                    for session in sessions
                    if "Second" in session["title"]
                )

                self.assertTrue(await service.delete_session(first_id))
                self.assertEqual(
                    len(await service.list_sessions()),
                    1,
                )

                self.assertTrue(await service.delete_session(second_id))
                self.assertEqual(await service.list_sessions(), [])
                snapshot = await service.snapshot()
                self.assertEqual(snapshot["phase"], "idle")
                self.assertEqual(snapshot["entries"], [])

                with self.assertRaises(ValueError):
                    await service.delete_session(second_id)
            finally:
                await service.close()

    async def test_rpc_dispatch_preserves_approval_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            stdout = io.StringIO()
            stderr = io.StringIO()
            server_ref: JsonRpcServer | None = None

            async def emit(event: dict[str, Any]) -> None:
                if server_ref is None:
                    raise AssertionError("server was not initialized")
                await server_ref._emit_ui_event(event)

            streams = ScriptedStreams(
                [
                    _assistant(
                        [
                            ToolCall(
                                id="write-rpc",
                                name="write_file",
                                arguments={
                                    "path": "rpc.txt",
                                    "content": "rpc",
                                },
                            )
                        ],
                        stop_reason="tool_use",
                    ),
                    _assistant([TextContent("RPC complete.")]),
                ]
            )
            service = SessionService(
                emit,
                model=MODEL,
                stream_fn=streams,
                storage_root=root / "state",
            )
            server = JsonRpcServer(
                stdout=stdout,
                stderr=stderr,
                sessions=service,
            )
            server_ref = server

            try:
                started = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "session.start",
                        "params": {
                            "prompt": "Create rpc.txt",
                            "workspaceRoot": str(workspace),
                        },
                    }
                )
                self.assertEqual(started["result"], {"accepted": True})

                await _wait_for(
                    lambda: any(
                        message.get("method") == "ui.event"
                        and message["params"]["event"]["snapshot"].get(
                            "pendingApprovals"
                        )
                        for message in rpc_messages(stdout)
                    )
                )
                messages = rpc_messages(stdout)
                pending_event = next(
                    message["params"]["event"]
                    for message in reversed(messages)
                    if message.get("method") == "ui.event"
                    and message["params"]["event"]["snapshot"].get(
                        "pendingApprovals"
                    )
                )
                approval = pending_event["snapshot"]["pendingApprovals"][0]

                resolved = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "approval.resolve",
                        "params": {
                            "approvalId": approval["id"],
                            "effectDigest": approval["effectDigest"],
                            "approved": True,
                        },
                    }
                )
                self.assertEqual(resolved["result"], {"resolved": True})
                await asyncio.wait_for(service.wait_for_idle(), timeout=3)
            finally:
                await service.close()

            self.assertEqual(
                (workspace / "rpc.txt").read_text(encoding="utf-8"),
                "rpc",
            )
            self.assertIn("ui.event", [m.get("method") for m in rpc_messages(stdout)])

def rpc_messages(stream: io.StringIO) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in stream.getvalue().splitlines():
        if line.strip():
            messages.append(json.loads(line))
    return messages


if __name__ == "__main__":
    unittest.main()

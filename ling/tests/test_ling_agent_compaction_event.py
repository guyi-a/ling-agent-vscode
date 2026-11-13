"""Tests for compaction persistence and session restore."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.types import Context, UserMessage

from ling_agent import (
    CompactionApplied,
    CommittedEventEnvelope,
    Session,
    SessionRecord,
    SQLiteSessionStore,
)
from ling_agent.compaction import CompactionRecord


async def _storage_round_trip(root: Path) -> None:
    store = SQLiteSessionStore(root / "sessions.db")
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-1",
                workspace_root=root,
                title="compaction",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        event = CompactionApplied(
            compaction_id="compaction-1",
            through_message_id="assistant:4",
            summary=UserMessage(content="raw summary", timestamp=10),
            replaced_message_ids=("user:0", "assistant:1"),
            replaced_count=2,
            estimated_tokens=123,
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-1",
                seq=1,
                event_id="event-1",
                run_id="run-1",
                turn_id="run-1",
                step=0,
                event=event,
            )
        )

        events = await store.load_committed("session-1")
    finally:
        await store.close()

    loaded = events[0].event
    assert isinstance(loaded, CompactionApplied)
    assert loaded.compaction_id == "compaction-1"
    assert loaded.through_message_id == "assistant:4"
    assert loaded.summary.content == "raw summary"
    assert loaded.replaced_message_ids == ("user:0", "assistant:1")
    assert loaded.replaced_count == 2
    assert loaded.estimated_tokens == 123


async def _session_load_restores_active(root: Path) -> None:
    store = SQLiteSessionStore(root / "sessions.db")
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-1",
                workspace_root=root,
                title="compaction",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-1",
                seq=1,
                event_id="event-1",
                run_id="run-1",
                turn_id="run-1",
                step=0,
                event=CompactionApplied(
                    compaction_id="compaction-1",
                    through_message_id="assistant:4",
                    summary=UserMessage(
                        content="raw summary",
                        timestamp=10,
                    ),
                    replaced_message_ids=("user:0", "assistant:1"),
                    replaced_count=2,
                    estimated_tokens=123,
                ),
            )
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-1",
                seq=2,
                event_id="event-2",
                run_id="run-2",
                turn_id="run-2",
                step=0,
                event=CompactionApplied(
                    compaction_id="compaction-2",
                    through_message_id="assistant:6",
                    summary=UserMessage(
                        content="newer summary",
                        timestamp=11,
                    ),
                    replaced_message_ids=("user:2", "assistant:3"),
                    replaced_count=2,
                    estimated_tokens=200,
                ),
            )
        )

        session = await Session.load(
            store,
            "session-1",
            context=Context(),
        )
    finally:
        await store.close()

    assert session.active_compaction is not None
    assert session.active_compaction.id == "compaction-2"
    assert session.active_compaction.summary == "newer summary"
    assert session.active_compaction.replaced_message_ids == (
        "user:0",
        "assistant:1",
        "user:2",
        "assistant:3",
    )
    assert session.active_compaction.replaced_count == 4


async def _session_commit_compaction(root: Path) -> None:
    store = SQLiteSessionStore(root / "sessions.db")
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-1",
                workspace_root=root,
                title="compaction",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        session = await Session.load(
            store,
            "session-1",
            context=Context(),
        )
        await session.start_run(
            UserMessage(content="hello", timestamp=1)
        )
        record = CompactionRecord(
            id="compaction-1",
            through_message_id="assistant:4",
            summary="raw summary",
            replaced_message_ids=("user:0", "assistant:1"),
            replaced_count=2,
            estimated_tokens=123,
        )
        await session.commit_compaction(record)

        events = await store.load_committed("session-1")
    finally:
        await store.close()

    assert session.active_compaction == record
    compacted = [
        envelope.event
        for envelope in events
        if isinstance(envelope.event, CompactionApplied)
    ]
    assert len(compacted) == 1
    assert compacted[0].summary.content == "raw summary"


async def _session_drops_stale_compaction(root: Path) -> None:
    store = SQLiteSessionStore(root / "sessions.db")
    try:
        await store.upsert_session(
            SessionRecord(
                session_id="session-1",
                workspace_root=root,
                title="stale compaction",
                status="idle",
                created_at=1,
                updated_at=1,
            )
        )
        await store.append_committed(
            CommittedEventEnvelope(
                session_id="session-1",
                seq=1,
                event_id="event-1",
                run_id="run-1",
                turn_id="run-1",
                step=0,
                event=CompactionApplied(
                    compaction_id="compaction-1",
                    through_message_id="assistant:9",
                    summary=UserMessage(
                        content="stale summary",
                        timestamp=10,
                    ),
                    replaced_message_ids=("user:8", "assistant:9"),
                    replaced_count=2,
                    estimated_tokens=123,
                ),
            )
        )
        session = await Session.load(
            store,
            "session-1",
            context=Context(
                messages=[
                    UserMessage(content="remaining", timestamp=1)
                ]
            ),
        )
    finally:
        await store.close()

    assert session.active_compaction is None


def test_compaction_event_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_storage_round_trip(Path(temp)))


def test_session_load_restores_active_compaction() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_session_load_restores_active(Path(temp)))


def test_session_commit_compaction() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_session_commit_compaction(Path(temp)))


def test_session_drops_compaction_with_stale_message_boundaries() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_session_drops_stale_compaction(Path(temp)))

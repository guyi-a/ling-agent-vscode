"""Tests for per-run workspace change ledgers and undo."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.types import ToolCall, UserMessage
from ling_agent import (
    ChangeConflictError,
    Session,
    SQLiteSessionStore,
    TimelineProjector,
)
from ling_core import PostToolUse, PreToolUse, ToolContext


async def _ledger_and_undo(root: Path) -> None:
    first = root / "first.txt"
    first.write_bytes(b"before\n")
    store = SQLiteSessionStore(root / "sessions.db")
    session = await Session.create(store, workspace_root=root)
    run_id = await session.start_run(
        UserMessage(content="edit files", timestamp=1)
    )
    session.begin_turn()
    context = ToolContext(step=1)

    edit = ToolCall(
        id="edit-1",
        name="edit_file",
        arguments={
            "path": "first.txt",
            "old_text": "before",
            "new_text": "middle",
        },
    )
    await session._pre_tool_hook(PreToolUse(call=edit, context=context))
    first.write_bytes(b"middle\n")
    await session._post_tool_hook(
        PostToolUse(call=edit, output="middle\n", context=context)
    )

    edit_again = ToolCall(
        id="edit-2",
        name="edit_file",
        arguments={
            "path": "first.txt",
            "old_text": "middle",
            "new_text": "after",
        },
    )
    await session._pre_tool_hook(
        PreToolUse(call=edit_again, context=context)
    )
    first.write_bytes(b"after\n")
    await session._post_tool_hook(
        PostToolUse(
            call=edit_again,
            output="after\n",
            context=context,
        )
    )

    created = root / "created.txt"
    write = ToolCall(
        id="write-1",
        name="write_file",
        arguments={"path": "created.txt", "content": "new\n"},
    )
    await session._pre_tool_hook(PreToolUse(call=write, context=context))
    created.write_bytes(b"new\n")
    await session._post_tool_hook(
        PostToolUse(call=write, output="new\n", context=context)
    )
    await session.stop_run("completed")

    events = await store.load_committed(session.session_id)
    committed = next(
        envelope.event
        for envelope in events
        if envelope.run_id == run_id
        and type(envelope.event).__name__ == "RunChangesCommitted"
    )
    assert len(committed.changes) == 2
    by_path = {change.path: change for change in committed.changes}
    assert by_path["first.txt"].before == "before\n"
    assert by_path["first.txt"].after == "after\n"
    assert by_path["first.txt"].added == 1
    assert by_path["first.txt"].removed == 1
    assert by_path["created.txt"].before_exists is False
    assert by_path["created.txt"].after == "new\n"

    assert await session.revert_changes(run_id) == 2
    assert first.read_bytes() == b"before\n"
    assert not created.exists()

    projector = TimelineProjector(session.session_id)
    for envelope in await store.load_committed(session.session_id):
        projector.push_committed(envelope)
    assert projector.snapshot().runs[run_id].changes_reverted
    await store.close()


async def _revert_conflict(root: Path) -> None:
    target = root / "target.txt"
    target.write_bytes(b"before\n")
    store = SQLiteSessionStore(root / "sessions.db")
    session = await Session.create(store, workspace_root=root)
    run_id = await session.start_run(
        UserMessage(content="edit", timestamp=1)
    )
    session.begin_turn()
    call = ToolCall(
        id="edit-1",
        name="edit_file",
        arguments={
            "path": "target.txt",
            "old_text": "before",
            "new_text": "after",
        },
    )
    context = ToolContext(step=1)
    await session._pre_tool_hook(PreToolUse(call=call, context=context))
    target.write_bytes(b"after\n")
    await session._post_tool_hook(
        PostToolUse(call=call, output="after\n", context=context)
    )
    await session.stop_run("completed")

    target.write_bytes(b"user edit\n")
    try:
        await session.revert_changes(run_id)
    except ChangeConflictError:
        pass
    else:
        raise AssertionError("Expected a change conflict")
    assert target.read_bytes() == b"user edit\n"
    await store.close()


def test_per_run_ledger_and_undo() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_ledger_and_undo(Path(temp)))


def test_revert_rejects_external_changes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_revert_conflict(Path(temp)))


if __name__ == "__main__":
    test_per_run_ledger_and_undo()
    test_revert_rejects_external_changes()

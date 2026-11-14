"""Tests for computer-use UI projections."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ling_agent import (
    ApprovalKind,
    ApprovalRequest,
    ApprovalTimelineItem,
    ComputerEffect,
    TimelineSnapshot,
    ToolTimelineItem,
)
from ling_rpc.ui_events import (
    _approval_request,
    _tool_entry,
    snapshot_to_ui,
)


def test_snapshot_projects_computer_session() -> None:
    snapshot = TimelineSnapshot(
        session_id="session-1",
        last_seq=0,
        items=[],
        runs={},
        frame_seq_by_run={},
    )
    event = snapshot_to_ui(
        snapshot,
        computer_session={
            "id": "control-1",
            "app": "notepad.exe",
            "windowTitle": "Untitled - Notepad",
            "windowId": "w-1",
            "startedAt": 1,
            "expiresAt": 2,
            "idleExpiresAt": 3,
        },
    )
    assert event["computerSession"]["id"] == "control-1"


def test_approval_projects_computer_takeover() -> None:
    with tempfile.TemporaryDirectory() as temp:
        request = ApprovalRequest(
            kind=ApprovalKind.COMPUTER,
            tool_name="computer_act",
            arguments={
                "window_id": "w-1",
                "snapshot_id": "snap-1",
                "actions": [{"type": "click", "ref": "@snap-1:e1"}],
            },
            reason="computer act (w-1)",
            workspace_root=Path(temp),
            effect=ComputerEffect(
                operation="act",
                classification="mutating",
                window_id="w-1",
                action="click",
            ),
            effect_digest="digest",
        )
        item = ApprovalTimelineItem(
            id="approval-1",
            run_id="run-1",
            turn_id="turn-1",
            created_seq=1,
            call_id="call-1",
            request=request,
        )
        payload = _approval_request(item)
        assert payload["kind"] == "computer-takeover"
        assert payload["computer"]["windowId"] == "w-1"
        assert payload["computer"]["firstAction"] == "click"
        assert "allowAlways" not in payload


def test_launch_projects_window_details() -> None:
    item = ToolTimelineItem(
        id="call-2",
        run_id="run-1",
        turn_id="turn-1",
        created_seq=2,
        name="computer_launch",
        arguments={"app": "notepad.exe"},
        output='{"ok": true}',
        details={
            "kind": "computer",
            "operation": "launch",
            "app": "notepad.exe",
            "title": "Untitled - Notepad",
            "windowId": "w-9",
            "pid": 4321,
            "timedOut": False,
            "reused": False,
            "preview": "Launched notepad.exe -> Untitled - Notepad",
        },
        status="completed",
    )
    entry = _tool_entry(item, waiting_for_approval=False)
    detail = entry["tool"]["detail"]
    assert entry["tool"]["action"] == "Computer"
    assert (
        entry["tool"]["target"]
        == 'Launch notepad.exe -> "Untitled - Notepad"'
    )
    assert detail["operation"] == "launch"
    assert detail["windowId"] == "w-9"
    assert detail["pid"] == 4321
    assert detail["timedOut"] is False


def test_computer_targets_describe_operations() -> None:
    base = {
        "id": "call-3",
        "run_id": "run-1",
        "turn_id": "turn-1",
        "created_seq": 3,
        "output": "{}",
        "status": "completed",
    }
    list_item = ToolTimelineItem(
        **base,
        name="computer_list",
        arguments={},
        details={
            "kind": "computer",
            "operation": "list",
            "windowCount": 5,
        },
    )
    assert (
        _tool_entry(list_item, waiting_for_approval=False)["tool"][
            "target"
        ]
        == "List windows · 5"
    )
    act_item = ToolTimelineItem(
        **base,
        name="computer_act",
        arguments={"window_id": "w-1"},
        details={
            "kind": "computer",
            "operation": "act",
            "windowId": "w-1",
            "title": "Untitled - Notepad",
            "app": "notepad.exe",
            "actionSummary": "click x2",
        },
    )
    assert (
        _tool_entry(act_item, waiting_for_approval=False)["tool"][
            "target"
        ]
        == 'Act click x2 in "Untitled - Notepad"'
    )
    screenshot_item = ToolTimelineItem(
        **base,
        name="computer_screenshot",
        arguments={"window_id": "w-1"},
        details={
            "kind": "computer",
            "operation": "screenshot",
            "windowId": "w-1",
            "title": "Untitled - Notepad",
            "app": "notepad.exe",
            "captureId": "cap-test",
            "width": 1280,
            "height": 720,
            "scale": 0.5,
            "method": "printwindow",
        },
    )
    screenshot_entry = _tool_entry(
        screenshot_item,
        waiting_for_approval=False,
    )
    assert (
        screenshot_entry["tool"]["target"]
        == 'Screenshot "Untitled - Notepad" · 1280x720'
    )
    assert screenshot_entry["tool"]["detail"]["captureId"] == "cap-test"
    assert screenshot_entry["tool"]["detail"]["scale"] == 0.5
    focus_item = ToolTimelineItem(
        **base,
        name="computer_focus",
        arguments={"window_id": "w-1"},
        details={
            "kind": "computer",
            "operation": "focus",
            "windowId": "w-1",
            "title": "Untitled - Notepad",
            "app": "notepad.exe",
            "foreground": False,
            "restored": True,
        },
    )
    focus_entry = _tool_entry(focus_item, waiting_for_approval=False)
    assert (
        focus_entry["tool"]["target"]
        == 'Focus "Untitled - Notepad" · not foreground'
    )
    assert focus_entry["tool"]["detail"]["foreground"] is False
    assert focus_entry["tool"]["detail"]["restored"] is True
    reuse_item = ToolTimelineItem(
        **base,
        name="computer_launch",
        arguments={"app": "notepad.exe"},
        details={
            "kind": "computer",
            "operation": "launch",
            "app": "notepad.exe",
            "title": "Untitled - Notepad",
            "reused": True,
        },
    )
    assert (
        _tool_entry(reuse_item, waiting_for_approval=False)["tool"][
            "target"
        ]
        == 'Reuse notepad.exe -> "Untitled - Notepad"'
    )
    failed_item = ToolTimelineItem(
        **{**base, "status": "failed"},
        name="computer_observe",
        arguments={"window_id": "w-1"},
        details={

            "kind": "computer",
            "operation": "observe",
            "windowId": "w-1",
            "nodeCount": 3,
            "error": "WINDOW_MINIMIZED",
        },
    )
    assert (
        _tool_entry(failed_item, waiting_for_approval=False)["tool"][
            "target"
        ]
        == "Observe w-1 · 3 nodes · WINDOW_MINIMIZED"
    )


if __name__ == "__main__":
    test_snapshot_projects_computer_session()
    test_approval_projects_computer_takeover()
    test_launch_projects_window_details()
    test_computer_targets_describe_operations()
    print("computer UI projection tests passed")

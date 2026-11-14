"""Tests for the computer-use audit trail."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ling_agent import (
    ComputerEffect,
    ComputerService,
    ComputerUseConfig,
)
from ling_agent.computer.audit import AuditLogger
from ling_agent.computer.service import _audit_actions


class AuditHelper:
    async def request(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        params = params or {}
        if method == "windows.list":
            return {
                "windows": [
                    {
                        "id": "w-1",
                        "title": "Notes",
                        "app": "notepad.exe",
                        "exeName": "notepad.exe",
                        "controllable": True,
                    }
                ],
                "displays": [],
                "helper": {"pid": 1, "version": "0.1.0"},
                "total": 1,
                "truncated": False,
            }
        if method == "control.begin":
            return {"active": True, "windowId": params.get("window_id", "")}
        if method == "window.observe":
            return {
                "observationId": "obs-1",
                "windowId": "w-1",
                "snapshotId": "snap-1",
                "captureId": "cap-1",
                "width": 100,
                "height": 100,
                "bytes": 10,
                "method": "printwindow",
                "window": {
                    "id": "w-1",
                    "title": "Notes",
                    "app": "notepad.exe",
                },
                "tree": "",
                "elements": [],
                "complete": True,
                "truncated": False,
                "identityUnproven": False,
                "nodeCount": 0,
            }
        if method == "input.act":
            return {
                "actions": [
                    {
                        "type": "type",
                        "ref": "@snap-1:e1",
                        "ok": True,
                        "mechanism": "clipboard",
                        "verified": True,
                    }
                ],
                "delivered": True,
                "mayHaveTakenEffect": True,
            }
        if method == "control.end":
            return {"stopped": True}
        return {}

    async def close(self, timeout: float = 0.4) -> None:
        return None


def _read_events(root: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(root.glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_audit_logger_writes_and_prunes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "computer"
        root.mkdir()
        old = root / "audit-2000-01-01.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        logger = AuditLogger(enabled=True, root=root, retention_days=30)
        logger.write(
            "action.requested",
            action="type",
            textLength=8,
        )
        assert not old.exists()
        events = _read_events(root)
        assert len(events) == 1
        assert events[0]["event"] == "action.requested"
        assert events[0]["textLength"] == 8
        assert "ts" in events[0]

        disabled = AuditLogger(enabled=False, root=root)
        disabled.write("action.requested", textLength=1)
        assert len(_read_events(root)) == 1


def test_service_audit_redacts_input() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "computer"
        service = ComputerService(
            ComputerUseConfig(audit_enabled=True, audit_root=str(root)),
            helper=AuditHelper(),
        )

        async def scenario() -> None:
            await service.list_windows()
            service.note_approval_granted(
                ComputerEffect(
                    operation="act",
                    classification="mutating",
                    window_id="w-1",
                    action="type",
                ),
                "approval-test",
            )
            await service.act(
                window_id="w-1",
                snapshot_id="snap-1",
                call_id="call-1",
                actions=[
                    {
                        "type": "type",
                        "ref": "@snap-1:e1",
                        "text": "secret text",
                    }
                ],
            )
            await service.stop("test")

        asyncio.run(scenario())
        events = _read_events(root)
        names = [event["event"] for event in events]
        assert names == [
            "session.started",
            "action.requested",
            "action.completed",
            "capture.requested",
            "capture.completed",
            "session.stopped",
        ]
        assert events[0]["approvalId"] == "approval-test"
        requested = events[1]
        assert requested["approvalId"] == "approval-test"
        assert requested["callId"] == "call-1"
        assert requested["windowId"] == "w-1"
        assert requested["actions"][0]["textLength"] == 11
        assert "text" not in requested["actions"][0]
        assert events[2]["actions"][0]["mechanism"] == "clipboard"
        assert events[-1]["reason"] == "test"


def test_audit_retention_cutoff() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        recent = root / (
            "audit-"
            + datetime.now(timezone.utc).strftime("%Y-%m-%d")
            + ".jsonl"
        )
        recent.write_text("", encoding="utf-8")
        stale = root / (
            "audit-"
            + (
                datetime.now(timezone.utc) - timedelta(days=40)
            ).strftime("%Y-%m-%d")
            + ".jsonl"
        )
        stale.write_text("", encoding="utf-8")
        AuditLogger(enabled=True, root=root, retention_days=30)
        assert recent.exists()
        assert not stale.exists()


def test_audit_records_chords_but_redacts_single_keys() -> None:
    chord = _audit_actions(
        [
            {
                "type": "press_key",
                "key": "s",
                "modifiers": ["ctrl"],
            }
        ]
    )[0]
    assert chord["key"] == "s"
    assert chord["modifiers"] == ["ctrl"]

    single = _audit_actions(
        [{"type": "press_key", "key": "a"}]
    )[0]
    assert single["keyRedacted"] is True
    assert single["keyLength"] == 1
    assert "key" not in single


if __name__ == "__main__":
    test_audit_logger_writes_and_prunes()
    test_service_audit_redacts_input()
    test_audit_retention_cutoff()
    test_audit_records_chords_but_redacts_single_keys()
    print("computer audit tests passed")

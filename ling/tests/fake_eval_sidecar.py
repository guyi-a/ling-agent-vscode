"""Small fake sidecar used by the headless runner tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _respond(request_id: Any, result: Any) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def _notify(event: dict[str, Any]) -> None:
    _send(
        {
            "jsonrpc": "2.0",
            "method": "ui.event",
            "params": {"event": event},
        }
    )


def _log(message: str) -> None:
    path = os.environ.get("FAKE_SIDECAR_LOG", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def _running_snapshot(prompt: str) -> dict[str, Any]:
    return {
        "phase": "running",
        "sessionId": "fake-session",
        "runs": [
            {
                "id": "run-1",
                "status": "running",
                "startedAt": int(time.time() * 1000),
            }
        ],
        "entries": [
            {
                "kind": "message",
                "runId": "run-1",
                "message": {
                    "id": "user-1",
                    "role": "user",
                    "content": prompt,
                    "streaming": False,
                },
            }
        ],
        "pendingApprovals": [],
        "pendingQuestions": [],
    }


def _completed_snapshot(prompt: str) -> dict[str, Any]:
    return {
        "phase": "idle",
        "sessionId": "fake-session",
        "runs": [
            {
                "id": "run-1",
                "status": "completed",
                "startedAt": int(time.time() * 1000),
                "completedAt": int(time.time() * 1000),
            }
        ],
        "entries": [
            {
                "kind": "message",
                "runId": "run-1",
                "message": {
                    "id": "user-1",
                    "role": "user",
                    "content": prompt,
                    "streaming": False,
                },
            },
            {
                "kind": "tool",
                "runId": "run-1",
                "tool": {
                    "id": "tool-1",
                    "name": "run_command",
                    "action": "Run",
                    "target": "fake-tool",
                    "status": "Completed",
                    "details": [],
                },
            },
            {
                "kind": "message",
                "runId": "run-1",
                "message": {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "fake done",
                    "streaming": False,
                },
            },
        ],
        "pendingApprovals": [],
        "pendingQuestions": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--mode",
        choices=("complete", "timeout", "approval", "fail"),
        default="complete",
    )
    args, _ = parser.parse_known_args()
    prompt = ""
    snapshot_count = 0
    started = False
    approval_resolved = False
    cancelled = False

    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            _respond(
                request_id,
                {
                    "protocolVersion": 1,
                    "serverInfo": {"name": "fake", "version": "1"},
                    "capabilities": {},
                },
            )
        elif method == "session.start":
            prompt = str(params.get("prompt", ""))
            workspace = Path(str(params.get("workspaceRoot", ".")))
            (workspace / "a.txt").write_text(
                (workspace / "a.txt").read_text(encoding="utf-8")
                + "changed by agent\n",
                encoding="utf-8",
            )
            (workspace / "new.txt").write_text(
                "new file\n",
                encoding="utf-8",
            )
            artifacts = workspace / ".agents"
            artifacts.mkdir(exist_ok=True)
            (artifacts / "spill.txt").write_text(
                "runtime artifact\n",
                encoding="utf-8",
            )
            started = True
            _log("start")
            _respond(request_id, {"accepted": True})
            if args.mode == "fail":
                _notify(
                    {
                        "type": "run.failed",
                        "message": "fake failure",
                    }
                )
        elif method == "session.snapshot":
            snapshot_count += 1
            if not started:
                _respond(
                    request_id,
                    {
                        "phase": "idle",
                        "sessionId": "",
                        "runs": [],
                        "entries": [],
                        "pendingApprovals": [],
                        "pendingQuestions": [],
                    },
                )
            elif cancelled:
                _respond(
                    request_id,
                    {
                        "phase": "cancelled",
                        "sessionId": "fake-session",
                        "runs": [
                            {"id": "run-1", "status": "cancelled"}
                        ],
                        "entries": [],
                        "pendingApprovals": [],
                        "pendingQuestions": [],
                    },
                )
            elif args.mode == "fail":
                _respond(
                    request_id,
                    {
                        "phase": "failed",
                        "sessionId": "fake-session",
                        "runs": [],
                        "entries": [],
                        "pendingApprovals": [],
                        "pendingQuestions": [],
                        "error": "fake failure",
                    },
                )
            elif args.mode == "timeout":
                _respond(request_id, _running_snapshot(prompt))
            elif args.mode == "approval" and not approval_resolved:
                snapshot = _running_snapshot(prompt)
                snapshot["pendingApprovals"] = [
                    {
                        "id": "approval-1",
                        "effectDigest": "digest-1",
                        "kind": "command",
                        "tool": "run_command",
                    }
                ]
                _respond(request_id, snapshot)
            elif snapshot_count < 2:
                _respond(request_id, _running_snapshot(prompt))
            else:
                _respond(request_id, _completed_snapshot(prompt))
        elif method == "approval.resolve":
            approval_resolved = True
            _log("approval")
            _respond(request_id, {"resolved": True})
        elif method == "question.answer":
            _log("question")
            _respond(request_id, {"resolved": True})
        elif method == "session.cancel":
            cancelled = True
            _log("cancel")
            _respond(request_id, {"cancelled": True})
        elif method == "shutdown":
            _log("shutdown")
            _respond(request_id, None)
            return 0
        else:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

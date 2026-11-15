"""Run one prompt against a headless Ling sidecar and export its patch."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ling_agent.events import AssistantMessageCommitted
from ling_agent.storage import (
    SQLiteSessionStore,
    default_database_path,
    default_storage_root,
)

from .rpc import (
    RpcError,
    RpcTimeout,
    SidecarClient,
    default_sidecar_command,
    sidecar_environment,
)

LING_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "cancelled", "error"}
)
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_POLL_SECONDS = 0.5


@dataclass(slots=True)
class HeadlessResult:
    """The observable outcome of one headless run."""

    status: str
    phase: str
    session_id: str
    prompt: str
    workspace: str
    model: str
    reasoning: str
    started_at: float
    finished_at: float
    duration_seconds: float
    base_revision: str
    patch: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    assistant_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    event_log: str = ""
    stderr_tail: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HeadlessResult":
        def _list(value: Any) -> list[dict[str, Any]]:
            return (
                [item for item in value if isinstance(item, dict)]
                if isinstance(value, list)
                else []
            )

        return cls(
            status=str(payload.get("status", "error")),
            phase=str(payload.get("phase", "unknown")),
            session_id=str(payload.get("sessionId", "")),
            prompt=str(payload.get("prompt", "")),
            workspace=str(payload.get("workspace", "")),
            model=str(payload.get("model", "")),
            reasoning=str(payload.get("reasoning", "")),
            started_at=float(payload.get("startedAt", 0) or 0),
            finished_at=float(payload.get("finishedAt", 0) or 0),
            duration_seconds=float(
                payload.get("durationSeconds", 0) or 0
            ),
            base_revision=str(payload.get("baseRevision", "")),
            patch=str(payload.get("patch", "")),
            entries=_list(payload.get("entries")),
            runs=_list(payload.get("runs")),
            assistant_text=str(payload.get("assistantText", "")),
            tool_calls=_list(payload.get("toolCalls")),
            usage=(
                dict(payload["usage"])
                if isinstance(payload.get("usage"), dict)
                else {}
            ),
            error=(
                str(payload["error"])
                if payload.get("error") is not None
                else None
            ),
            event_log=str(payload.get("eventLog", "")),
            stderr_tail=str(payload.get("stderrTail", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "phase": self.phase,
            "sessionId": self.session_id,
            "prompt": self.prompt,
            "workspace": self.workspace,
            "model": self.model,
            "reasoning": self.reasoning,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "durationSeconds": self.duration_seconds,
            "baseRevision": self.base_revision,
            "patch": self.patch,
            "patchBytes": len(self.patch.encode("utf-8")),
            "entries": self.entries,
            "runs": self.runs,
            "assistantText": self.assistant_text,
            "toolCalls": self.tool_calls,
            "usage": self.usage,
            "error": self.error,
            "eventLog": self.event_log,
            "stderrTail": self.stderr_tail,
        }


def _terminal_run(
    snapshot: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    runs = snapshot.get("runs")
    if not isinstance(runs, list) or not runs:
        return None
    last = runs[-1]
    if not isinstance(last, Mapping):
        return None
    if last.get("status") not in TERMINAL_RUN_STATUSES:
        return None
    return last


def _tool_calls(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("kind") != "tool":
            continue
        tool = entry.get("tool")
        if isinstance(tool, dict):
            calls.append(tool)
    return calls


def _assistant_text(entries: list[dict[str, Any]]) -> str:
    for entry in reversed(entries):
        if entry.get("kind") != "message":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def git_revision(workspace: Path) -> str:
    """Return the current HEAD revision, or an empty string."""

    result = subprocess.run(
        ["git", "-C", str(workspace.resolve()), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def export_patch(
    workspace: Path,
    *,
    base: str | None = None,
    extra_excludes: Sequence[str] = (),
) -> str:
    """Return a git patch including tracked edits and new files."""

    root = workspace.resolve()
    if not (root / ".git").exists():
        return ""
    common = [
        "git",
        "-C",
        str(root),
        "--no-pager",
    ]
    excludes = [".", ":(exclude).agents"]
    for path in extra_excludes:
        try:
            relative = (root / path).resolve().relative_to(root)
        except ValueError:
            relative = Path(path)
        excludes.append(f":(exclude){relative.as_posix()}")
    subprocess.run(
        [*common, "add", "-N", "-A", "--", *excludes],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    target = base or "HEAD"
    result = subprocess.run(
        [
            *common,
            "diff",
            "--binary",
            "--no-color",
            "--no-ext-diff",
            target,
            "--",
            *excludes,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        if base:
            return ""
        head = subprocess.run(
            [*common, "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if head.returncode == 0:
            return ""
        result = subprocess.run(
            [
                *common,
                "diff",
                "--binary",
                "--no-color",
                "--no-ext-diff",
                "--",
                *excludes,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    return result.stdout if result.returncode == 0 else ""


async def _read_usage(
    storage_root: Path,
    session_id: str,
) -> dict[str, Any]:
    """Sum token usage from committed assistant messages."""

    if not session_id:
        return {}
    database = default_database_path(storage_root)
    if not database.is_file():
        return {}
    store = SQLiteSessionStore(database)
    try:
        events = await store.load_committed(session_id)
    except Exception:
        return {}
    finally:
        await store.close()
    totals = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "cost": 0.0,
        "modelCalls": 0,
    }
    for envelope in events:
        event = envelope.event
        if not isinstance(event, AssistantMessageCommitted):
            continue
        usage = event.message.usage
        totals["modelCalls"] += 1
        for source, target in (
            ("input_tokens", "inputTokens"),
            ("output_tokens", "outputTokens"),
            ("cache_read_tokens", "cacheReadTokens"),
            ("cache_write_tokens", "cacheWriteTokens"),
            ("total_tokens", "totalTokens"),
        ):
            value = getattr(usage, source, 0)
            if isinstance(value, int):
                totals[target] += value
        cost = getattr(usage, "cost", 0.0)
        if isinstance(cost, (int, float)):
            totals["cost"] += float(cost)
    if totals["totalTokens"] <= 0:
        totals["totalTokens"] = (
            totals["inputTokens"] + totals["outputTokens"]
        )
    return totals


async def _resolve_pending(
    client: SidecarClient,
    snapshot: Mapping[str, Any] | None,
    seen_approvals: set[str],
    seen_questions: set[str],
) -> None:
    if not isinstance(snapshot, Mapping):
        return
    approvals = snapshot.get("pendingApprovals")
    if isinstance(approvals, list):
        for approval in approvals:
            if not isinstance(approval, dict):
                continue
            approval_id = approval.get("id")
            digest = approval.get("effectDigest")
            if (
                not isinstance(approval_id, str)
                or not approval_id
                or not isinstance(digest, str)
                or not digest
                or approval_id in seen_approvals
            ):
                continue
            seen_approvals.add(approval_id)
            approved = approval.get("kind") != "computer-takeover"
            try:
                await client.request(
                    "approval.resolve",
                    {
                        "approvalId": approval_id,
                        "effectDigest": digest,
                        "approved": approved,
                        "remember": False,
                    },
                    timeout=10.0,
                )
            except (RpcError, RpcTimeout):
                continue
    questions = snapshot.get("pendingQuestions")
    if isinstance(questions, list):
        for question in questions:
            if not isinstance(question, dict):
                continue
            call_id = question.get("callId")
            if (
                not isinstance(call_id, str)
                or not call_id
                or call_id in seen_questions
            ):
                continue
            seen_questions.add(call_id)
            try:
                await client.request(
                    "question.answer",
                    {"callId": call_id, "cancelled": True},
                    timeout=10.0,
                )
            except (RpcError, RpcTimeout):
                continue


def _build_env(
    *,
    isolate: bool,
    temp_root: Path | None,
    model: str | None,
    base_url: str | None,
    max_steps: int | None,
    command_timeout_seconds: float | None,
    extra_env: Mapping[str, str] | None,
) -> dict[str, str]:
    env = sidecar_environment()
    if extra_env:
        env.update(extra_env)
    env["LING_COMPUTER_ENABLED"] = "0"
    env.setdefault("LING_SKILLS_ROOT", "")
    if model:
        env["LING_MODEL"] = model
    if base_url:
        env["LING_API_BASE_URL"] = base_url
    if max_steps is not None:
        env["LING_MAX_STEPS"] = str(max_steps)
    if command_timeout_seconds is not None:
        env["LING_COMMAND_TIMEOUT_SECONDS"] = str(
            command_timeout_seconds
        )
    if isolate and temp_root is not None:
        storage = temp_root / "storage"
        skills = temp_root / "skills"
        global_skills = temp_root / "global-skills"
        global_memory = temp_root / "global-memory"
        mcp_state = temp_root / "mcp-state"
        for path in (
            storage,
            skills,
            global_skills,
            global_memory,
            mcp_state,
        ):
            path.mkdir(parents=True, exist_ok=True)
        mcp_config = temp_root / "mcp.json"
        mcp_config.write_text(
            json.dumps({"mcpServers": {}}),
            encoding="utf-8",
        )
        env["LING_STORAGE_ROOT"] = str(storage)
        env["LING_SKILLS_ROOT"] = str(skills)
        env["LING_GLOBAL_SKILLS_ROOT"] = str(global_skills)
        env["LING_GLOBAL_MEMORY_ROOT"] = str(global_memory)
        env["LING_MCP_STATE_ROOT"] = str(mcp_state)
        env["LING_MCP_CONFIG"] = str(mcp_config)
    return env


async def run_headless(
    workspace: str | Path,
    prompt: str,
    *,
    sidecar_command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    approval_mode: str = "auto",
    model: str | None = None,
    base_url: str | None = None,
    reasoning: str | None = None,
    max_steps: int | None = None,
    max_tokens: int | None = None,
    command_timeout_seconds: float | None = None,
    isolate: bool = True,
    event_log: str | Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> HeadlessResult:
    """Run one prompt and return the final status, trace, and patch."""

    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Workspace does not exist: {root}")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    command = list(sidecar_command or default_sidecar_command())
    event_path = (
        Path(event_log).expanduser().resolve() if event_log else None
    )
    temp = (
        tempfile.TemporaryDirectory(prefix="ling-eval-")
        if isolate
        else None
    )
    temp_root = Path(temp.name) if temp is not None else None
    env = _build_env(
        isolate=isolate,
        temp_root=temp_root,
        model=model,
        base_url=base_url,
        max_steps=max_steps,
        command_timeout_seconds=command_timeout_seconds,
        extra_env=extra_env,
    )
    env.setdefault("PYTHONPATH", str(LING_ROOT))
    client = SidecarClient(
        command,
        env=env,
        cwd=LING_ROOT,
        event_log=event_path,
    )
    storage_root = (
        temp_root / "storage"
        if temp_root is not None
        else Path(env.get("LING_STORAGE_ROOT") or default_storage_root())
    )
    started_at = time.time()
    base_revision = git_revision(root)
    snapshot: dict[str, Any] | None = None
    terminal: Mapping[str, Any] | None = None
    seen_approvals: set[str] = set()
    seen_questions: set[str] = set()
    usage: dict[str, Any] = {}
    status = "error"
    run_error: str | None = None
    try:
        await client.start()
        await client.request(
            "initialize",
            {"protocolVersion": 1},
            timeout=20.0,
        )
        start_params: dict[str, Any] = {
            "prompt": prompt,
            "workspaceRoot": str(root),
            "approvalMode": approval_mode,
        }
        if reasoning is not None:
            start_params["reasoning"] = reasoning
        await client.request(
            "session.start",
            start_params,
            timeout=60.0,
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout_seconds)
        while loop.time() < deadline:
            snapshot = await client.request(
                "session.snapshot",
                {},
                timeout=30.0,
            )
            await _resolve_pending(
                client,
                snapshot,
                seen_approvals,
                seen_questions,
            )
            if max_tokens is not None:
                usage = await _read_usage(
                    storage_root,
                    str(snapshot.get("sessionId", "")),
                )
                if usage.get("totalTokens", 0) > max_tokens:
                    terminal = {
                        "status": "timeout",
                        "error": (
                            f"Token budget exceeded: "
                            f"{usage.get('totalTokens', 0)} > "
                            f"{max_tokens}"
                        ),
                    }
                    try:
                        await client.request(
                            "session.cancel",
                            {},
                            timeout=10.0,
                        )
                    except (RpcError, RpcTimeout):
                        pass
                    break
            terminal = _terminal_run(snapshot)
            if terminal is not None:
                break
            if client.failure_message:
                terminal = {
                    "status": "error",
                    "error": client.failure_message,
                }
                break
            if client.cancelled:
                terminal = {"status": "cancelled"}
                break
            await asyncio.sleep(poll_seconds)

        status = "timeout"
        if terminal is not None:
            status = str(terminal.get("status", "error"))
        else:
            try:
                await client.request(
                    "session.cancel",
                    {},
                    timeout=10.0,
                )
            except (RpcError, RpcTimeout):
                pass
            end = loop.time() + 10.0
            while loop.time() < end:
                snapshot = await client.request(
                    "session.snapshot",
                    {},
                    timeout=30.0,
                )
                await _resolve_pending(
                    client,
                    snapshot,
                    seen_approvals,
                    seen_questions,
                )
                terminal = _terminal_run(snapshot)
                if terminal is not None or client.failure_message:
                    break
                await asyncio.sleep(0.2)

        try:
            final_snapshot = await client.request(
                "session.snapshot",
                {},
                timeout=30.0,
            )
            if isinstance(final_snapshot, dict):
                snapshot = final_snapshot
        except (RpcError, RpcTimeout):
            pass
        try:
            usage = await _read_usage(
                storage_root,
                str(snapshot.get("sessionId", ""))
                if isinstance(snapshot, dict)
                else "",
            )
        except Exception:
            pass
    except (RpcError, RpcTimeout, OSError, RuntimeError) as exc:
        run_error = str(exc) or type(exc).__name__
        status = "error"
    finally:
        try:
            await client.close()
        except (OSError, RpcError, RpcTimeout):
            pass
        finally:
            if temp is not None:
                temp.cleanup()

    finished_at = time.time()
    entries = (
        snapshot.get("entries", [])
        if isinstance(snapshot, dict)
        and isinstance(snapshot.get("entries"), list)
        else []
    )
    runs = (
        snapshot.get("runs", [])
        if isinstance(snapshot, dict)
        and isinstance(snapshot.get("runs"), list)
        else []
    )
    error = run_error or client.failure_message
    if (
        error is None
        and isinstance(terminal, Mapping)
        and terminal.get("error")
    ):
        error = str(terminal["error"])
    if (
        error is None
        and isinstance(snapshot, dict)
        and snapshot.get("error")
    ):
        error = str(snapshot["error"])
    if status == "timeout" and not error:
        error = f"Run exceeded {timeout_seconds:.0f}s"
    extra_excludes: list[str] = []
    if event_path is not None:
        try:
            extra_excludes.append(str(event_path.relative_to(root)))
        except ValueError:
            pass
    return HeadlessResult(
        status=status,
        phase=str(snapshot.get("phase", "unknown"))
        if isinstance(snapshot, dict)
        else "unknown",
        session_id=str(snapshot.get("sessionId", ""))
        if isinstance(snapshot, dict)
        else "",
        prompt=prompt,
        workspace=str(root),
        model=model or "",
        reasoning=reasoning or "",
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(finished_at - started_at, 3),
        base_revision=base_revision,
        patch=export_patch(
            root,
            base=base_revision or None,
            extra_excludes=extra_excludes,
        ),
        entries=entries,
        runs=runs,
        assistant_text=_assistant_text(entries),
        tool_calls=_tool_calls(entries),
        usage=usage,
        error=error,
        event_log=str(event_path) if event_path else "",
        stderr_tail=client.stderr_tail,
    )


__all__ = [
    "HeadlessResult",
    "export_patch",
    "git_revision",
    "run_headless",
]

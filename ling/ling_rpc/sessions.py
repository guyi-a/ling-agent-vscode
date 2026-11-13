"""Session lifecycle and RPC approval bridge for the sidecar."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from ling_agent import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    AssistantMessageCommitted,
    CodingAgent,
    CompactionApplied,
    HarnessConfig,
    ProcessExecEffect,
    RunChangesCommitted,
    RunStarted,
    RunStopped,
    SQLiteSessionStore,
    TimelineProjector,
    UserAttachment,
    UserMessageCommitted,
    UserSelection,
    default_global_memory_root,
    default_database_path,
)
from ling_agent.background_tasks import BackgroundTaskManager
from ling_agent.browser_bridge import BrowserBridge
from ling_agent.compaction import CompactionConfig, SummarizeFn
from ling_agent.events import BackgroundTaskNotice
from ling_agent.local_settings import (
    LocalSettingsStore,
    command_allow_patterns,
)
from ling_agent.mcp import McpConfigStore, McpManager
from ling_agent.skill_sync import sync_bundled_skills
from ling_agent.skills import global_skills_root
from ling_agent.storage import BackgroundTaskRecord
from ling_agent.subagents import SubagentRunner
from ling_agent.workspace import Workspace
from ling_ai.stream import StreamFunction
from ling_ai.types import Model, TextContent

from .provider import resolve_embedder, resolve_model, resolve_web_client
from .questions import RpcQuestionHandler
from .ui_events import EmitUiEvent, RpcEventSink, UiEvent, snapshot_to_ui


@dataclass(slots=True)
class _PendingApproval:
    effect_digest: str
    future: asyncio.Future[ApprovalDecision]
    request: ApprovalRequest | None = None


class RpcApprovalHandler:
    """Wait for `approval.resolve` RPCs and bind them to effect digests."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingApproval] = {}
        self._feedback: dict[str, str] = {}

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        future: asyncio.Future[ApprovalDecision] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request.approval_id] = _PendingApproval(
            effect_digest=request.effect_digest,
            future=future,
            request=request,
        )
        try:
            return await future
        finally:
            self._pending.pop(request.approval_id, None)

    async def resolve(
        self,
        approval_id: str,
        effect_digest: str,
        approved: bool,
        feedback: str | None = None,
    ) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            return False
        if pending.effect_digest != effect_digest:
            return False
        if not approved and feedback:
            self._feedback[approval_id] = feedback
        pending.future.set_result(
            ApprovalDecision.ALLOW_ONCE
            if approved
            else ApprovalDecision.DENY
        )
        return True

    def pending_request(
        self,
        approval_id: str,
    ) -> ApprovalRequest | None:
        pending = self._pending.get(approval_id)
        return pending.request if pending is not None else None

    def feedback_for(self, approval_id: str) -> str | None:
        return self._feedback.pop(approval_id, None)


@dataclass(slots=True)
class _QueuedPrompt:
    id: str
    prompt: str
    attachments: tuple[UserAttachment, ...]
    selections: tuple[UserSelection, ...]
    active_paths: tuple[str, ...]
    approval_mode: ApprovalMode
    reasoning: str | None = None
    display_text: str | None = None
    kind: str = "prompt"
    tasks: tuple[BackgroundTaskNotice, ...] = ()
    task_ids: tuple[str, ...] = ()


MAX_QUEUED_PROMPTS = 20


def _background_task_view(
    task: BackgroundTaskRecord,
) -> dict[str, Any]:
    return {
        "id": task.id,
        "sessionId": task.session_id,
        "toolCallId": task.tool_call_id,
        "description": task.description,
        "status": task.status,
        "summary": task.summary,
        "error": task.error,
        "createdAt": task.created_at,
        "startedAt": task.started_at,
        "completedAt": task.completed_at,
        "kind": task.kind,
        "exitCode": task.exit_code,
        "outputPath": task.output_path,
    }

EmitIndexProgress = Callable[[dict[str, Any]], Awaitable[None]]


class SessionService:
    """Own the active coding agent and expose it through RPC-friendly methods."""

    def __init__(
        self,
        emit_event: EmitUiEvent,
        *,
        model: Model | None = None,
        stream_fn: StreamFunction | None = None,
        config: HarnessConfig | None = None,
        storage_root: str | Path | None = None,
        summarize_fn: SummarizeFn | None = None,
        emit_index_progress: EmitIndexProgress | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._emit_index_progress = emit_index_progress
        self._configured_model = model
        self._configured_stream = stream_fn
        self._config = config or _config_from_env()
        self._summarize_fn = summarize_fn
        self._embedder = resolve_embedder()
        self._browser_bridge = BrowserBridge(
            port=self._config.browser_bridge_port,
            command_timeout=(
                self._config.browser_bridge_command_timeout_seconds
            ),
        )
        resolved_storage_root = (
            storage_root
            or os.environ.get("LING_STORAGE_ROOT")
            or (config.storage_root if config is not None else None)
        )
        self._storage_root = resolved_storage_root
        skills_root = os.environ.get("LING_SKILLS_ROOT", "").strip()
        self._builtin_skills_root = (
            Path(skills_root).expanduser() if skills_root else None
        )
        self._global_skills_root = (
            global_skills_root()
            if self._builtin_skills_root is not None
            else None
        )
        self._skill_sync = (
            sync_bundled_skills(
                self._builtin_skills_root,
                self._global_skills_root,
            )
            if self._global_skills_root is not None
            else None
        )
        if self._skill_sync is not None:
            for message in self._skill_sync.diagnostics:
                print(f"skill sync: {message}", file=sys.stderr)
        self._history_store = SQLiteSessionStore(
            default_database_path(resolved_storage_root)
        )
        self._background_tasks = BackgroundTaskManager(
            self._history_store,
            self._make_background_runner,
            self._config,
            on_updated=self._emit_background_updated,
            on_terminal=self._handle_background_terminal,
        )
        self._background_schedule_task: asyncio.Task[None] | None = None
        self._background_continuation_pending = False
        self._agent: CodingAgent | None = None
        self._approvals: RpcApprovalHandler | None = None
        self._questions: RpcQuestionHandler | None = None
        self._task: asyncio.Task[None] | None = None
        self._queue: list[_QueuedPrompt] = []
        self._promote_pending = False
        self._workspace_root: Path | None = None
        self._session_id: str | None = None
        self._approval_mode = ApprovalMode.MANUAL
        self._recovery_lock = asyncio.Lock()
        self._recovery_done = False
        self._closed = False

    async def _ensure_recovered(self) -> None:
        if self._recovery_done:
            return
        async with self._recovery_lock:
            if self._recovery_done:
                return
            await self._history_store.recover_interrupted_approvals()
            await self._background_tasks.start()
            self._recovery_done = True

    def _make_background_runner(
        self,
        workspace: Workspace,
    ) -> SubagentRunner:
        model, stream_fn = self._resolve_model()
        return SubagentRunner(
            model=model,
            stream_fn=stream_fn,
            workspace=workspace,
            config=self._config,
            embedder=self._embedder,
        )

    async def _emit_background_updated(
        self,
        task: BackgroundTaskRecord,
    ) -> None:
        if self._closed or task.session_id != self._session_id:
            return
        await self._emit_event(
            {
                "type": "background.updated",
                "task": _background_task_view(task),
            }
        )

    async def _handle_background_terminal(
        self,
        session_id: str,
    ) -> None:
        if self._closed or session_id != self._session_id:
            return
        self._background_continuation_pending = True
        if (
            self._background_schedule_task is not None
            and not self._background_schedule_task.done()
        ):
            return
        self._background_schedule_task = asyncio.create_task(
            self._delayed_background_continuation(),
            name="ling-background-continuation-schedule",
        )

    async def _delayed_background_continuation(self) -> None:
        try:
            await asyncio.sleep(
                self._config.background_task_notify_debounce_ms / 1000
            )
            self._background_schedule_task = None
            await self._maybe_start_background_continuation()
        except asyncio.CancelledError:
            return

    async def _maybe_start_background_continuation(self) -> None:
        if self._closed or self._session_id is None:
            return
        if self._task is not None and not self._task.done():
            return
        if self._agent is None:
            if self._workspace_root is None:
                return
            await self._ensure_agent(self._workspace_root)
        agent = self._agent
        if agent is None:
            return
        item = await self._claim_background_item()
        if item is None:
            self._background_continuation_pending = False
            return
        if self._task is not None and not self._task.done():
            self._queue.insert(0, item)
            return
        self._task = asyncio.create_task(
            self._run_queue(agent, item),
            name="ling-background-continuation",
        )

    async def _claim_background_item(
        self,
    ) -> _QueuedPrompt | None:
        if self._session_id is None:
            return None
        tasks = await self._background_tasks.claim_notifications(
            self._session_id
        )
        if not tasks:
            return None
        notices = tuple(
            BackgroundTaskNotice(
                id=task.id,
                description=task.description,
                status=task.status,
                summary=task.summary,
                error=task.error,
                kind=task.kind,
                exit_code=task.exit_code,
                log_path=task.output_path,
            )
            for task in tasks
        )
        return _QueuedPrompt(
            id=f"background-{uuid.uuid4()}",
            prompt="",
            attachments=(),
            selections=(),
            active_paths=(),
            approval_mode=self._approval_mode,
            kind="background",
            tasks=notices,
            task_ids=tuple(task.id for task in tasks),
        )

    async def _run_background(
        self,
        agent: CodingAgent,
        item: _QueuedPrompt,
    ) -> tuple[str, bool]:
        started = False

        def mark_started() -> None:
            nonlocal started
            started = True

        try:
            result = await agent.continue_background_tasks(
                item.tasks,
                on_started=mark_started,
            )
        except asyncio.CancelledError:
            if not started:
                await self._background_tasks.release_notifications(
                    list(item.task_ids)
                )
            await self._emit_event({"type": "run.cancelled"})
            raise
        except Exception as exc:
            if not started:
                await self._background_tasks.release_notifications(
                    list(item.task_ids)
                )
            await self._emit_event(
                {
                    "type": "run.failed",
                    "message": str(exc) or type(exc).__name__,
                }
            )
            return "error", started
        if result.stop_reason == "error":
            await self._emit_event(
                {
                    "type": "run.failed",
                    "message": result.error_message
                    or "The agent run failed",
                }
            )
            return "error", started
        if result.stop_reason == "aborted":
            await self._emit_event({"type": "run.cancelled"})
            return "aborted", started
        return "completed", started

    async def start(
        self,
        prompt: str,
        workspace_root: str,
        *,
        approval_mode: ApprovalMode | None = None,
        attachments: Sequence[UserAttachment] = (),
        selections: Sequence[UserSelection] = (),
        active_paths: Sequence[str] = (),
        reasoning: str | None = None,
        display_text: str | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Session service is closed")
        if display_text is not None and not isinstance(
            display_text, str
        ):
            raise ValueError("displayText must be a string")
        if not prompt.strip() and not attachments and not selections:
            raise ValueError("prompt must not be empty")
        if reasoning == "auto":
            reasoning = None
        if reasoning not in (
            None,
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError(
                "reasoning must be one of: auto, low, medium, high, "
                "xhigh, max"
            )
        if self._task is not None and not self._task.done():
            root = Path(workspace_root).expanduser().resolve()
            if self._workspace_root is not None and root != self._workspace_root:
                raise RuntimeError(
                    "The active session belongs to another workspace"
                )
            if len(self._queue) >= MAX_QUEUED_PROMPTS:
                raise ValueError("Too many queued messages")
            item = _QueuedPrompt(
                id=f"queued-{uuid.uuid4()}",
                prompt=prompt,
                attachments=tuple(attachments),
                selections=tuple(selections),
                active_paths=tuple(active_paths),
                approval_mode=approval_mode or self._approval_mode,
                reasoning=reasoning,
                display_text=display_text,
            )
            self._queue.append(item)
            await self._emit_queue()
            return {
                "accepted": True,
                "queued": True,
                "queueLength": len(self._queue),
            }

        await self._ensure_recovered()
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")

        if approval_mode is not None:
            self._approval_mode = approval_mode
        agent = await self._ensure_agent(root)
        session = await agent.ensure_session()
        if session.approval_mode != self._approval_mode:
            await session.set_approval_mode(self._approval_mode)
        self._session_id = session.session_id
        item = _QueuedPrompt(
            id=f"queued-{uuid.uuid4()}",
            prompt=prompt,
            attachments=tuple(attachments),
            selections=tuple(selections),
            active_paths=tuple(active_paths),
            approval_mode=self._approval_mode,
            reasoning=reasoning,
            display_text=display_text,
        )
        self._task = asyncio.create_task(
            self._run_queue(agent, item),
            name="ling-session-run",
        )
        return {"accepted": True}

    async def set_approval_mode(self, mode: ApprovalMode) -> ApprovalMode:
        if self._closed:
            raise RuntimeError("Session service is closed")
        agent = self._agent
        if agent is not None and agent.session is not None:
            await agent.set_approval_mode(mode)
        self._approval_mode = mode
        return mode

    async def resolve_approval(
        self,
        approval_id: str,
        effect_digest: str,
        approved: bool,
        *,
        remember: bool = False,
        feedback: str | None = None,
    ) -> bool:
        if self._approvals is None:
            return False
        request = self._approvals.pending_request(approval_id)
        if approved and remember and request is not None:
            command = request.arguments.get("command")
            effect = request.effect
            if (
                request.tool_name == "run_command"
                and isinstance(command, str)
                and isinstance(effect, ProcessExecEffect)
                and effect.classification != "destructive"
            ):
                patterns = command_allow_patterns(command)
                if patterns:
                    try:
                        LocalSettingsStore(
                            request.workspace_root
                        ).allow_commands(patterns)
                    except (OSError, ValueError) as exc:
                        print(
                            f"unable to save command allow rule: {exc}",
                            file=sys.stderr,
                        )
        return await self._approvals.resolve(
            approval_id,
            effect_digest,
            approved,
            feedback=feedback,
        )

    async def resolve_question(
        self,
        call_id: str,
        answers: object = None,
        *,
        cancelled: bool = False,
    ) -> bool:
        if self._questions is None:
            return False
        return await self._questions.resolve(
            call_id,
            answers,
            cancelled=cancelled,
        )

    async def run_changes(
        self,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Return the persisted before/after snapshots for one run."""

        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        events = await self._history_store.load_committed(session_id)
        committed = next(
            (
                envelope
                for envelope in events
                if envelope.run_id == run_id
                and isinstance(envelope.event, RunChangesCommitted)
            ),
            None,
        )
        if committed is None:
            return {"runId": run_id, "changes": []}
        return {
            "runId": run_id,
            "changes": [
                {
                    "path": change.path,
                    "beforeExists": change.before_exists,
                    "before": change.before,
                    "afterExists": change.after_exists,
                    "after": change.after,
                    "added": change.added,
                    "removed": change.removed,
                }
                for change in committed.event.changes
            ],
        }

    async def revert_run_changes(
        self,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Undo one run after checking its recorded output is still current."""

        if self._task is not None and not self._task.done():
            raise RuntimeError("Cancel the active run before undo")
        if self._session_id != session_id:
            raise RuntimeError("Open this conversation before undo")
        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        agent = self._agent
        if agent is None:
            agent = await self._ensure_agent(record.workspace_root)
        if agent.session is None:
            await agent.ensure_session()
        session = agent.session
        if session is None:
            raise RuntimeError("Open this conversation before undo")
        count = await session.revert_changes(run_id)
        return {"reverted": True, "count": count}

    async def mcp_status(self, workspace_root: str) -> dict[str, Any]:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        agent = self._agent
        if (
            agent is not None
            and self._workspace_root == root
            and agent.session is not None
        ):
            status = await agent.mcp_manager.status()
        else:
            manager = McpManager(root)
            try:
                status = await manager.status()
            finally:
                await manager.close()
        return {
            "configPath": status.config_path,
            "servers": [
                {
                    "name": server.name,
                    "kind": server.kind,
                    "target": server.target,
                    "enabled": server.enabled,
                    "state": server.state,
                    "auth": server.auth,
                    **(
                        {"authorized": server.authorized}
                        if server.authorized is not None
                        else {}
                    ),
                    "toolCount": server.tool_count,
                    "tools": list(server.tools),
                    **(
                        {"error": server.error}
                        if server.error
                        else {}
                    ),
                }
                for server in status.servers
            ],
            "issues": [
                {
                    "name": issue.name,
                    "message": issue.message,
                }
                for issue in status.issues
            ],
        }

    async def background_list(
        self,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        target = session_id or self._session_id
        if not target:
            return []
        tasks = await self._background_tasks.list(target)
        return [_background_task_view(task) for task in tasks]

    async def background_tool_call(
        self,
        call_id: str,
    ) -> dict[str, Any]:
        agent = self._agent
        if (
            agent is None
            or self._task is None
            or self._task.done()
            or self._session_id is None
        ):
            raise RuntimeError("No active run")
        if (
            agent.session is not None
            and agent.session.session_id != self._session_id
        ):
            raise RuntimeError("The command belongs to another session")
        task = await agent.background_tool_call(call_id)
        return _background_task_view(task)

    async def stop_background_task(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        task = await self._background_tasks.get(task_id)
        if task is None:
            raise ValueError(f"Unknown background task: {task_id}")
        if self._session_id is not None and task.session_id != self._session_id:
            raise ValueError("Background task belongs to another session")
        updated = await self._background_tasks.stop(task_id)
        if updated is None:
            raise ValueError(f"Unknown background task: {task_id}")
        return _background_task_view(updated)

    async def ensure_mcp_config(self, workspace_root: str) -> dict[str, Any]:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        store = McpConfigStore(root)
        path = store.ensure_file()
        return {"path": str(path)}

    async def authorize_mcp_server(
        self,
        workspace_root: str,
        name: str,
    ) -> dict[str, Any]:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        agent = self._agent
        manager = None
        owned = False
        if (
            agent is not None
            and self._workspace_root == root
            and agent.session is not None
        ):
            manager = agent.mcp_manager
        else:
            manager = McpManager(root)
            owned = True
        try:
            status = await manager.authorize_server(name)
            return {
                "name": status.name,
                "state": status.state,
                "authorized": status.authorized,
                "toolCount": status.tool_count,
                "tools": list(status.tools),
            }
        finally:
            if owned:
                await manager.close()

    async def revoke_mcp_auth(
        self,
        workspace_root: str,
        name: str,
    ) -> dict[str, Any]:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        agent = self._agent
        manager = None
        owned = False
        if (
            agent is not None
            and self._workspace_root == root
            and agent.session is not None
        ):
            manager = agent.mcp_manager
        else:
            manager = McpManager(root)
            owned = True
        try:
            status = await manager.revoke_server_auth(name)
            return {
                "revoked": True,
                "servers": len(status.servers),
            }
        finally:
            if owned:
                await manager.close()

    async def cancel(self) -> None:
        had_queue = bool(self._queue)
        queued_tasks = [
            task_id
            for item in self._queue
            if item.kind == "background"
            for task_id in item.task_ids
        ]
        self._queue.clear()
        if queued_tasks:
            await self._background_tasks.release_notifications(
                queued_tasks
            )
        self._promote_pending = False
        if had_queue:
            await self._emit_queue()
        agent = self._agent
        if agent is None:
            return
        await agent.cancel()
        task = self._task
        if task is not None and not task.done():
            await asyncio.shield(task)

    async def new_session(self) -> None:
        await self._ensure_recovered()
        await self._dispose_agent()
        self._approval_mode = ApprovalMode.MANUAL
        await self._emit_event(
            {
                "type": "session.snapshot",
                "snapshot": {
                    "phase": "idle",
                    "entries": [],
                    "pendingApprovals": [],
                    "pendingQuestions": [],
                    "queuedMessages": [],
                    "backgroundTasks": [],
                    "error": None,
                    "readOnly": False,
                    "approvalMode": self._approval_mode.value,
                },
            }
        )

    async def list_sessions(
        self,
        workspace_root: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        await self._ensure_recovered()
        root = _resolved_workspace_root(workspace_root)
        records = await self._history_store.list_sessions()
        sessions: list[dict[str, Any]] = []
        for record in records:
            if root is not None and record.workspace_root != root:
                continue
            if len(sessions) >= 50:
                break
            title = record.title
            if not title:
                events = await self._history_store.load_committed(
                    record.session_id
                )
                first_user = next(
                    (
                        envelope.event
                        for envelope in events
                        if isinstance(envelope.event, UserMessageCommitted)
                    ),
                    None,
                )
                if first_user is not None:
                    title = (
                        first_user.display_text
                        or _content_text(first_user.message.content)
                    )
                    if not title and first_user.attachments:
                        title = first_user.attachments[0].name
            sessions.append(
                {
                    "id": record.session_id,
                    "title": _truncate(title or record.workspace_root.name, 68),
                    "workspaceRoot": str(record.workspace_root),
                    "updatedAt": record.updated_at,
                    "status": record.status,
                }
            )
        return sessions

    async def search_sessions(
        self,
        query: str,
        *,
        workspace_root: str | Path | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self._ensure_recovered()
        needle = query.strip().lower()
        if not needle:
            return await self.list_sessions(workspace_root)

        root = _resolved_workspace_root(workspace_root)
        results: list[dict[str, Any]] = []
        for record in await self._history_store.list_sessions():
            if root is not None and record.workspace_root != root:
                continue
            events = await self._history_store.load_committed(
                record.session_id
            )
            title = record.title
            first_user_text: str | None = None
            texts: list[str] = []
            for envelope in events:
                event = envelope.event
                if isinstance(event, UserMessageCommitted):
                    text = (
                        event.display_text
                        if event.display_text is not None
                        else _content_text(event.message.content)
                    )
                    if first_user_text is None and text:
                        first_user_text = text
                    if text:
                        texts.append(text)
                    texts.extend(
                        attachment.name
                        for attachment in event.attachments
                    )
                    continue
                if isinstance(event, AssistantMessageCommitted):
                    text = _assistant_text(event.message.content)
                else:
                    continue
                if text:
                    texts.append(text)

            resolved_title = _truncate(
                title or first_user_text or record.workspace_root.name,
                68,
            )
            haystack = "\n".join([resolved_title, *texts])
            if needle not in haystack.lower():
                continue

            results.append(
                {
                    "id": record.session_id,
                    "title": resolved_title,
                    "workspaceRoot": str(record.workspace_root),
                    "updatedAt": record.updated_at,
                    "status": record.status,
                    "snippet": _search_snippet(haystack, needle),
                }
            )
            if len(results) >= limit:
                break
        return results

    async def delete_session(self, session_id: str) -> bool:
        if self._task is not None and not self._task.done():
            raise RuntimeError(
                "Cancel the active run before deleting history"
            )
        await self._ensure_recovered()
        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")

        active = self._session_id == session_id
        if (
            self._agent is not None
            and self._agent.session is not None
            and self._agent.session.session_id == session_id
        ):
            active = True

        if active:
            await self._dispose_agent()
            self._session_id = None
            self._approval_mode = ApprovalMode.MANUAL

        for task in await self._background_tasks.list(session_id):
            if task.status in ("pending", "running"):
                await self._background_tasks.stop(task.id)

        deleted = await self._history_store.delete_session(session_id)
        if not deleted:
            raise ValueError(f"Unknown session: {session_id}")

        if active:
            await self._emit_event(
                {
                    "type": "session.snapshot",
                    "snapshot": {
                        "phase": "idle",
                        "entries": [],
                        "pendingApprovals": [],
                        "pendingQuestions": [],
                        "queuedMessages": [],
                        "backgroundTasks": [],
                        "error": None,
                        "readOnly": False,
                        "approvalMode": self._approval_mode.value,
                    },
                }
            )
        return True

    async def history_snapshot(self, session_id: str) -> dict[str, Any]:
        await self._ensure_recovered()
        return await self._project_history(session_id, read_only=True)

    async def open_session(
        self,
        session_id: str,
        *,
        workspace_root: str | Path | None = None,
    ) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            raise RuntimeError("Cancel the active run before opening history")
        await self._ensure_recovered()
        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        root = _resolved_workspace_root(workspace_root)
        if root is not None and record.workspace_root != root:
            raise RuntimeError(
                "This conversation belongs to another workspace: "
                f"{record.workspace_root}. Open that folder to continue it."
            )
        await self._dispose_agent()
        self._workspace_root = record.workspace_root
        self._session_id = session_id
        self._approval_mode = record.approval_mode
        await self._handle_background_terminal(session_id)
        return await self._project_history(session_id, read_only=False)

    async def delete_message(self, message_id: str) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            raise RuntimeError(
                "Cancel the active run before deleting a message"
            )
        session_id = self._session_id
        if session_id is None:
            raise ValueError("No active session")
        events = await self._history_store.load_committed(session_id)
        target = next(
            (
                envelope
                for envelope in events
                if envelope.message_id == message_id
            ),
            None,
        )
        if target is None:
            raise ValueError(f"Unknown message: {message_id}")

        seqs = {target.seq}
        seqs.update(
            envelope.seq
            for envelope in events
            if isinstance(envelope.event, CompactionApplied)
        )
        if isinstance(target.event, AssistantMessageCommitted):
            for envelope in events:
                if envelope.turn_id != target.turn_id:
                    continue
                if isinstance(envelope.event, (RunStarted, RunStopped)):
                    continue
                seqs.add(envelope.seq)

        await self._history_store.delete_events(
            session_id, tuple(sorted(seqs))
        )
        await self._reload_active_session(session_id)
        return {"deleted": True}

    async def regenerate_message(
        self,
        message_id: str,
        *,
        reasoning: str | None = None,
    ) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            raise RuntimeError(
                "Cancel the active run before regenerating a message"
            )
        if reasoning == "auto":
            reasoning = None
        if reasoning not in (
            None,
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError(
                "reasoning must be one of: auto, low, medium, high, "
                "xhigh, max"
            )

        session_id = self._session_id
        if session_id is None:
            raise ValueError("No active session")
        events = await self._history_store.load_committed(session_id)
        target = next(
            (
                envelope
                for envelope in events
                if envelope.message_id == message_id
            ),
            None,
        )
        if target is None:
            raise ValueError(f"Unknown message: {message_id}")
        if not isinstance(target.event, AssistantMessageCommitted):
            raise ValueError("Only assistant messages can be regenerated")
        if any(
            envelope.seq > target.seq
            and isinstance(
                envelope.event,
                (UserMessageCommitted, AssistantMessageCommitted),
            )
            for envelope in events
        ):
            raise ValueError(
                "Only the latest assistant message can be regenerated"
            )

        user_message = next(
            (
                envelope
                for envelope in reversed(events)
                if envelope.seq < target.seq
                and isinstance(envelope.event, UserMessageCommitted)
            ),
            None,
        )
        if user_message is None:
            raise ValueError("No user message to regenerate")

        await self._history_store.delete_events_after(
            session_id, user_message.seq
        )
        run_started = next(
            (
                envelope
                for envelope in events
                if isinstance(envelope.event, RunStarted)
                and envelope.run_id == user_message.run_id
            ),
            None,
        )
        if run_started is not None:
            await self._history_store.delete_events(
                session_id, (run_started.seq,)
            )

        await self._reload_active_session(session_id)
        agent = self._agent
        if agent is None:
            raise RuntimeError("Unable to reload the session")
        self._task = asyncio.create_task(
            self._run(
                agent,
                "",
                reasoning=reasoning,
                regenerate=True,
            ),
            name="ling-session-regenerate",
        )
        return {"accepted": True}

    async def _reload_active_session(self, session_id: str) -> None:
        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        approval_mode = record.approval_mode
        await self._dispose_agent()
        self._workspace_root = record.workspace_root
        self._session_id = session_id
        self._approval_mode = approval_mode
        await self._ensure_agent(record.workspace_root)
        await self._handle_background_terminal(session_id)
        snapshot = await self.snapshot()
        await self._emit_event(
            {
                "type": "session.snapshot",
                "snapshot": snapshot,
            }
        )

    async def snapshot(self) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Session service is closed")
        await self._ensure_recovered()
        agent = self._agent
        if agent is not None and agent.session is not None:
            timeline = await agent.timeline_snapshot()
            if timeline is not None:
                return snapshot_to_ui(
                    timeline,
                    approval_mode=self._approval_mode.value,
                    pending_questions=(
                        self._questions.pending()
                        if self._questions is not None
                        else ()
                    ),
                    queued_messages=self.queue_view(),
                    background_tasks=await self.background_list(),
                )
        if self._session_id is not None:
            return await self._project_history(
                self._session_id,
                read_only=False,
            )
        return {
            "phase": "idle",
            "entries": [],
            "pendingApprovals": [],
            "pendingQuestions": [],
            "queuedMessages": [],
            "backgroundTasks": [],
            "error": None,
            "readOnly": False,
            "approvalMode": self._approval_mode.value,
        }

    async def _project_history(
        self,
        session_id: str,
        *,
        read_only: bool,
    ) -> dict[str, Any]:
        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        events = await self._history_store.load_committed(session_id)
        projector = TimelineProjector(session_id)
        for envelope in events:
            projector.push_committed(envelope)
        snapshot = snapshot_to_ui(
            projector.snapshot(),
            approval_mode=record.approval_mode.value,
            background_tasks=await self.background_list(session_id),
        )
        snapshot["sessionId"] = session_id
        snapshot["readOnly"] = read_only
        return snapshot

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if (
            self._background_schedule_task is not None
            and not self._background_schedule_task.done()
        ):
            self._background_schedule_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._background_schedule_task
        await self._dispose_agent()
        await self._background_tasks.close()
        await self._browser_bridge.stop()
        await self._history_store.close()

    async def wait_for_idle(self) -> None:
        task = self._task
        if task is not None:
            await task

    async def _ensure_agent(self, root: Path) -> CodingAgent:
        if (
            self._agent is not None
            and self._workspace_root is not None
            and self._workspace_root != root
        ):
            raise RuntimeError(
                "The active session belongs to another workspace; create a new session"
            )

        if self._agent is not None:
            return self._agent

        self._workspace_root = root
        self._approvals = RpcApprovalHandler()
        self._questions = RpcQuestionHandler(self._emit_event)
        sink = RpcEventSink(
            self._emit_event,
            approval_mode=lambda: self._approval_mode.value,
            queued_messages=self.queue_view,
            background_tasks=self.background_list,
        )
        model, stream_fn = self._resolve_model()
        self._agent = CodingAgent(
            model=model,
            stream_fn=stream_fn,
            workspace_root=root,
            storage_root=self._storage_root,
            config=self._config,
            approvals=self._approvals,
            event_sink=sink,
            summarize_fn=self._summarize_fn,
            builtin_skills_root=self._builtin_skills_root,
            global_skills_root=self._global_skills_root,
            questions=self._questions,
            embedder=self._embedder,
            subagent_trace_sink=self._emit_subagent_trace,
            browser_bridge=self._browser_bridge,
            web_client=resolve_web_client(model),
            global_memory_root=default_global_memory_root(),
            background_tasks=self._background_tasks,
        )
        if self._session_id is not None:
            await self._agent.resume(self._session_id)
        return self._agent

    def _resolve_model(self) -> tuple[Model, StreamFunction]:
        return resolve_model(
            self._configured_model,
            self._configured_stream,
        )

    async def _emit_subagent_trace(
        self,
        call_id: str,
        entry: dict[str, Any],
    ) -> None:
        await self._emit_event(
            {
                "type": "subagent.trace",
                "callId": call_id,
                "entry": entry,
            }
        )

    async def build_code_index(self, workspace_root: str) -> dict:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        from ling_retrieval.index import CodeIndex

        index = CodeIndex(root)
        loop = asyncio.get_running_loop()
        emit = self._emit_index_progress

        def progress(stats: dict) -> None:
            if emit is not None:
                asyncio.run_coroutine_threadsafe(emit(stats), loop)

        return await asyncio.to_thread(
            index.build,
            embedder=self._embedder,
            progress=progress,
        )

    async def code_index_status(self, workspace_root: str) -> dict:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {root}")
        from ling_retrieval.index import CodeIndex

        index = CodeIndex(root)
        return await asyncio.to_thread(
            index.status,
            embedder=self._embedder,
        )

    async def _run_queue(
        self,
        agent: CodingAgent,
        item: _QueuedPrompt,
    ) -> None:
        while True:
            if self._approval_mode != item.approval_mode:
                self._approval_mode = item.approval_mode
                if agent.session is not None:
                    await agent.session.set_approval_mode(
                        item.approval_mode
                    )
            if item.kind == "background":
                status, started = await self._run_background(agent, item)
                if status == "error" and not started:
                    # The continuation never began; stop here instead of
                    # immediately reclaiming the same released tasks.
                    self._background_continuation_pending = False
                    await self._emit_queue()
                    return
            else:
                status = await self._run(
                    agent,
                    item.prompt,
                    item.attachments,
                    item.selections,
                    item.active_paths,
                    item.reasoning,
                    display_text=item.display_text,
                )

            if status != "completed" and item.kind != "background":
                if status == "aborted" and self._promote_pending and self._queue:
                    self._promote_pending = False
                else:
                    self._promote_pending = False
                    self._queue.clear()
                    await self._emit_queue()
                    return
            else:
                self._promote_pending = False

            background_item = await self._claim_background_item()
            if background_item is not None:
                item = background_item
                continue

            if not self._queue:
                await self._emit_queue()
                return

            item = self._queue.pop(0)
            await self._emit_queue()

    async def _run(
        self,
        agent: CodingAgent,
        prompt: str,
        attachments: Sequence[UserAttachment] = (),
        selections: Sequence[UserSelection] = (),
        active_paths: Sequence[str] = (),
        reasoning: str | None = None,
        *,
        regenerate: bool = False,
        display_text: str | None = None,
    ) -> str:
        try:
            if regenerate:
                result = await agent.regenerate(reasoning=reasoning)
            else:
                result = await agent.prompt(
                    prompt,
                    attachments=attachments,
                    selections=selections,
                    active_paths=active_paths,
                    reasoning=reasoning,
                    display_text=display_text,
                )
        except asyncio.CancelledError:
            await self._emit_event({"type": "run.cancelled"})
            raise
        except Exception as exc:
            await self._emit_event(
                {
                    "type": "run.failed",
                    "message": str(exc) or type(exc).__name__,
                }
            )
            return "error"
        else:
            if result.stop_reason == "error":
                await self._emit_event(
                    {
                        "type": "run.failed",
                        "message": result.error_message
                        or "The agent run failed",
                    }
                )
                return "error"
            if result.stop_reason == "aborted":
                await self._emit_event({"type": "run.cancelled"})
                return "aborted"
            return "completed"

    async def send_queued_now(self, queued_id: str) -> bool:
        index = next(
            (
                index
                for index, item in enumerate(self._queue)
                if item.id == queued_id and item.kind == "prompt"
            ),
            None,
        )
        if index is None:
            return False

        item = self._queue.pop(index)
        if (
            self._task is not None
            and not self._task.done()
            and self._agent is not None
        ):
            self._queue.insert(0, item)
            self._promote_pending = True
            await self._emit_queue()
            await self._agent.cancel()
            return True

        if self._agent is None:
            return False
        self._task = asyncio.create_task(
            self._run_queue(self._agent, item),
            name="ling-session-run",
        )
        await self._emit_queue()
        return True

    def queue_view(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "text": item.display_text or item.prompt,
                "attachmentCount": len(item.attachments),
            }
            for item in self._queue
            if item.kind == "prompt"
        ]

    async def _emit_queue(self) -> None:
        await self._emit_event(
            {
                "type": "session.queue",
                "queuedMessages": self.queue_view(),
            }
        )

    async def _dispose_agent(self) -> None:
        task = self._task
        self._task = None
        agent = self._agent
        questions = self._questions
        self._agent = None
        self._approvals = None
        self._questions = None
        self._workspace_root = None
        self._session_id = None
        self._queue.clear()
        self._promote_pending = False

        if agent is not None:
            await agent.cancel()
            await agent.close()
        if questions is not None:
            await questions.cancel_all("Session closed")
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass


__all__ = ["RpcApprovalHandler", "SessionService"]


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.text
            for block in content
            if hasattr(block, "text") and isinstance(block.text, str)
        )
    return ""


def _assistant_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(
        block.text
        for block in content
        if isinstance(block, TextContent)
    )


def _search_snippet(text: str, needle: str, radius: int = 60) -> str:
    index = text.lower().find(needle)
    if index < 0:
        return _truncate(text, 120)
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    snippet = " ".join(text[start:end].split())
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet


def _truncate(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _resolved_workspace_root(
    value: str | Path | None,
) -> Path | None:
    if value is None:
        return None
    if not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _config_from_env() -> HarnessConfig:
    def int_env(
        name: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if minimum <= value <= maximum else default

    def float_env(
        name: str,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return value if minimum <= value <= maximum else default

    return HarnessConfig(
        agent_max_steps=int_env("LING_MAX_STEPS", 50, 1, 100),
        agent_max_parallel_tools=int_env(
            "LING_MAX_PARALLEL_TOOLS", 4, 1, 16
        ),
        command_timeout_seconds=float_env(
            "LING_COMMAND_TIMEOUT_SECONDS", 120.0, 1.0, 3600.0
        ),
        approval_timeout_seconds=float_env(
            "LING_APPROVAL_TIMEOUT_SECONDS", 120.0, 1.0, 3600.0
        ),
        browser_bridge_port=int_env(
            "LING_BROWSER_BRIDGE_PORT", 17_323, 0, 65_535
        ),
        browser_bridge_connect_wait_seconds=float_env(
            "LING_BROWSER_BRIDGE_WAIT_SECONDS", 3.0, 0.0, 60.0
        ),
        browser_bridge_command_timeout_seconds=float_env(
            "LING_BROWSER_BRIDGE_COMMAND_TIMEOUT_SECONDS", 30.0, 1.0, 300.0
        ),
        compaction=CompactionConfig(
            window_nominal_tokens=int_env(
                "LING_CONTEXT_WINDOW_TOKENS", 1_000_000, 1024, 10_000_000
            ),
            keep_last_user_turns=int_env(
                "LING_KEEP_LAST_USER_TURNS", 5, 0, 50
            ),
        ),
    )

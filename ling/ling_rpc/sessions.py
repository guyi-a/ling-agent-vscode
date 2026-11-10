"""Session lifecycle and RPC approval bridge for the sidecar."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ling_agent import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    AssistantMessageCommitted,
    CodingAgent,
    HarnessConfig,
    SQLiteSessionStore,
    TimelineProjector,
    UserAttachment,
    UserMessageCommitted,
    UserSelection,
    default_database_path,
)
from ling_agent.compaction import SummarizeFn
from ling_ai.adapters.openai_completions import (
    create_openai_completions_stream,
)
from ling_ai.stream import StreamFunction
from ling_ai.types import Model, TextContent

from .questions import RpcQuestionHandler
from .ui_events import EmitUiEvent, RpcEventSink, UiEvent, snapshot_to_ui


@dataclass(slots=True)
class _PendingApproval:
    effect_digest: str
    future: asyncio.Future[ApprovalDecision]


class RpcApprovalHandler:
    """Wait for `approval.resolve` RPCs and bind them to effect digests."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingApproval] = {}

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        future: asyncio.Future[ApprovalDecision] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request.approval_id] = _PendingApproval(
            effect_digest=request.effect_digest,
            future=future,
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
    ) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            return False
        if pending.effect_digest != effect_digest:
            return False
        pending.future.set_result(
            ApprovalDecision.ALLOW_ONCE
            if approved
            else ApprovalDecision.DENY
        )
        return True


@dataclass(slots=True)
class _QueuedPrompt:
    id: str
    prompt: str
    attachments: tuple[UserAttachment, ...]
    selections: tuple[UserSelection, ...]
    approval_mode: ApprovalMode


MAX_QUEUED_PROMPTS = 20


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
    ) -> None:
        self._emit_event = emit_event
        self._configured_model = model
        self._configured_stream = stream_fn
        self._config = config
        self._summarize_fn = summarize_fn
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
        self._history_store = SQLiteSessionStore(
            default_database_path(resolved_storage_root)
        )
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
            self._recovery_done = True

    async def start(
        self,
        prompt: str,
        workspace_root: str,
        *,
        approval_mode: ApprovalMode | None = None,
        attachments: Sequence[UserAttachment] = (),
        selections: Sequence[UserSelection] = (),
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Session service is closed")
        if not prompt.strip() and not attachments and not selections:
            raise ValueError("prompt must not be empty")
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
                approval_mode=approval_mode or self._approval_mode,
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
            approval_mode=self._approval_mode,
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
    ) -> bool:
        if self._approvals is None:
            return False
        return await self._approvals.resolve(
            approval_id,
            effect_digest,
            approved,
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

    async def cancel(self) -> None:
        had_queue = bool(self._queue)
        self._queue.clear()
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
                    "error": None,
                    "readOnly": False,
                    "approvalMode": self._approval_mode.value,
                },
            }
        )

    async def list_sessions(self) -> list[dict[str, Any]]:
        await self._ensure_recovered()
        records = await self._history_store.list_sessions()
        sessions: list[dict[str, Any]] = []
        for record in records[:50]:
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
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self._ensure_recovered()
        needle = query.strip().lower()
        if not needle:
            return await self.list_sessions()

        results: list[dict[str, Any]] = []
        for record in await self._history_store.list_sessions():
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

    async def open_session(self, session_id: str) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            raise RuntimeError("Cancel the active run before opening history")
        await self._ensure_recovered()
        record = await self._history_store.get_session(session_id)
        if record is None:
            raise ValueError(f"Unknown session: {session_id}")
        await self._dispose_agent()
        self._workspace_root = record.workspace_root
        self._session_id = session_id
        self._approval_mode = record.approval_mode
        return await self._project_history(session_id, read_only=False)

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
        )
        snapshot["sessionId"] = session_id
        snapshot["readOnly"] = read_only
        return snapshot

    async def close(self) -> None:
        if self._closed:
            return
        await self._dispose_agent()
        self._closed = True
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
            questions=self._questions,
        )
        if self._session_id is not None:
            await self._agent.resume(self._session_id)
        return self._agent

    def _resolve_model(self) -> tuple[Model, StreamFunction]:
        if self._configured_stream is not None:
            model = self._configured_model or Model(
                id="test-model",
                provider="test",
                api="openai-completions",
            )
            return model, self._configured_stream

        api_key = (
            os.environ.get("LING_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "Missing API key. Set LING_API_KEY or DEEPSEEK_API_KEY."
            )

        model = self._configured_model or Model(
            id=os.environ.get("LING_MODEL", "deepseek-chat"),
            provider=os.environ.get("LING_PROVIDER", "deepseek"),
            api="openai-completions",
            base_url=os.environ.get(
                "LING_API_BASE_URL",
                "https://api.deepseek.com/v1",
            ),
        )
        return (
            model,
            create_openai_completions_stream(api_key=api_key),
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
            status = await self._run(
                agent,
                item.prompt,
                item.attachments,
                item.selections,
            )

            if status != "completed":
                if status == "aborted" and self._promote_pending and self._queue:
                    self._promote_pending = False
                else:
                    self._promote_pending = False
                    self._queue.clear()
                    await self._emit_queue()
                    return
            else:
                self._promote_pending = False

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
    ) -> str:
        try:
            result = await agent.prompt(
                prompt,
                attachments=attachments,
                selections=selections,
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
                if item.id == queued_id
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
                "text": item.prompt,
                "attachmentCount": len(item.attachments),
            }
            for item in self._queue
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

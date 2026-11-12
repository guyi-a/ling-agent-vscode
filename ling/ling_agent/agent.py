"""Public coding-agent integration facade."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from ling_ai.stream import StreamFunction, StreamOptions
from ling_ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)
from ling_core import (
    Agent,
    AgentInputBlocked,
    AgentLoopLimitError,
    HookRegistry,
    PostToolUse,
    PreToolUse,
)

from .approvals import (
    ApprovalDecision,
    ApprovalHandler,
    ApprovalRequest,
    create_approval_hook,
)
from .browser_bridge import BrowserBridge
from .attachments import (
    MAX_ATTACHMENT_FILE_BYTES,
    MAX_USER_ATTACHMENTS,
    UserAttachment,
    friendly_provider_error,
)
from .compaction import (
    CompactionRecord,
    CompactionRow,
    SummarizeFn,
    build_summarizer_messages,
    maybe_compact,
    project_model_messages,
    rows_from_messages,
    summary_instructions,
    wrap_summary,
)
from .config import HarnessConfig
from .effects import ApprovalMode
from .events import (
    AssistantMessageCommitted,
    EventSink,
    ToolResultCommitted,
    UserMessageCommitted,
)
from .memory import MemoryStore
from .plans import PlanStore
from .prompt import build_system_prompt
from .questions import QuestionHandler
from .selections import (
    MAX_USER_SELECTIONS,
    UserSelection,
)
from .session import Session, SessionApprovalHandler
from .skills import (
    Skill,
    load_skills,
    workspace_skills_root,
)
from .subagents import SubagentRunner, TraceSink
from .storage import SQLiteSessionStore, default_database_path
from .streaming import StreamFrameBuffer
from .timeline import TimelineProjector, TimelineSnapshot
from .web import WebClient
from .tools import (
    create_browser_tools,
    create_command_tools,
    create_document_tools,
    create_file_tools,
    create_mutation_tools,
    create_plan_tools,
    create_question_tools,
    create_search_tools,
    create_skill_tools,
    create_subagent_tools,
    create_memory_tools,
    create_web_tools,
)
from .tools.spill import create_spill_hook
from .workspace import Workspace

if TYPE_CHECKING:
    from ling_retrieval.embedding import Embedder


def _now_ms() -> int:
    return int(time.time() * 1000)


def _selection_context(selections: Sequence[UserSelection]) -> str:
    blocks: list[str] = []
    for selection in selections:
        lines = (
            f"{selection.line_count} line"
            if selection.line_count == 1
            else f"{selection.line_count} lines"
        )
        if selection.kind == "editor":
            location = selection.label
            if selection.start_line is not None:
                end_line = selection.end_line or selection.start_line
                location += f" (lines {selection.start_line}-{end_line}, {lines})"
            else:
                location += f" ({lines})"
        elif selection.kind == "terminal":
            location = f"Terminal: {selection.label} ({lines})"
        else:
            location = f"Chat: {selection.label} ({lines})"
        blocks.append(f"--- {location} ---\n{selection.text}")
    return "Selected context (quoted by the user):\n\n" + "\n\n".join(blocks)


class _DenyAllApprovalHandler:
    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.DENY


class CodingAgent:
    """Assemble the full coding-agent harness over one workspace."""

    def __init__(
        self,
        *,
        model: Model,
        stream_fn: StreamFunction,
        workspace_root: str | Path,
        storage_root: str | Path | None = None,
        config: HarnessConfig | None = None,
        approvals: ApprovalHandler | None = None,
        event_sink: EventSink | None = None,
        summarize_fn: SummarizeFn | None = None,
        builtin_skills_root: str | Path | None = None,
        questions: QuestionHandler | None = None,
        embedder: "Embedder | None" = None,
        subagent_trace_sink: TraceSink | None = None,
        browser_bridge: BrowserBridge | None = None,
        web_client: WebClient | None = None,
        global_memory_root: str | Path | None = None,
    ) -> None:
        self.model = model
        self.stream_fn = stream_fn
        self.config = config or HarnessConfig()
        self.workspace = Workspace(workspace_root, self.config)
        self.plans = PlanStore(self.workspace.root)
        self.approvals = approvals or _DenyAllApprovalHandler()
        self.event_sink = event_sink
        self.summarize_fn = summarize_fn
        self.questions = questions
        self.embedder = embedder
        self.browser_bridge = browser_bridge
        self.web_client = web_client
        self.global_memory_root = (
            Path(global_memory_root).expanduser().resolve()
            if global_memory_root is not None
            else None
        )
        self.memory_store = MemoryStore(
            self.workspace.root,
            global_root=self.global_memory_root,
        )
        self.subagents = SubagentRunner(
            model=model,
            stream_fn=stream_fn,
            workspace=self.workspace,
            config=self.config,
            embedder=embedder,
            trace_sink=subagent_trace_sink,
        )
        self._builtin_skills_root = (
            Path(builtin_skills_root).expanduser().resolve()
            if builtin_skills_root is not None
            else None
        )
        skill_result = load_skills(
            self._builtin_skills_root,
            self.workspace.root,
        )
        self.skills: tuple[Skill, ...] = skill_result.skills
        self.skill_diagnostics = skill_result.diagnostics
        self._skill_read_roots = tuple(
            root
            for root in (
                self._builtin_skills_root,
                workspace_skills_root(self.workspace.root),
            )
            if root is not None and root.is_dir()
        )
        resolved_storage_root = (
            storage_root
            if storage_root is not None
            else self.config.storage_root
        )
        self.store = SQLiteSessionStore(
            default_database_path(resolved_storage_root)
        )
        self._session: Session | None = None
        self._init_lock = asyncio.Lock()
        self._prompt_lock = asyncio.Lock()
        self._cancel_event: asyncio.Event | None = None
        self._closed = False

    @property
    def session(self) -> Session | None:
        return self._session

    async def _ensure_session(self) -> Session:
        if self._closed:
            raise RuntimeError("CodingAgent is closed")
        if self._session is not None:
            return self._session

        async with self._init_lock:
            if self._session is not None:
                return self._session
            await self.store.recover_interrupted_approvals()
            tools = self._create_tools()
            prompt = build_system_prompt(
                self.workspace,
                config=self.config,
                tool_names=[tool.definition.name for tool in tools],
                skills=self.skills,
                global_memory_root=self.global_memory_root,
            )
            context = Context(system_prompt=prompt.system_prompt)
            self._session = await Session.create(
                self.store,
                workspace_root=self.workspace.root,
                context=context,
                sink=self.event_sink,
                frames=StreamFrameBuffer(),
            )
            return self._session

    async def ensure_session(self) -> Session:
        return await self._ensure_session()

    async def resume(self, session_id: str) -> Session:
        if self._closed:
            raise RuntimeError("CodingAgent is closed")
        if self._session is not None:
            if self._session.session_id == session_id:
                return self._session
            raise RuntimeError("CodingAgent already owns another session")

        async with self._init_lock:
            if self._session is not None:
                if self._session.session_id == session_id:
                    return self._session
                raise RuntimeError("CodingAgent already owns another session")

            await self.store.recover_interrupted_approvals()
            record = await self.store.get_session(session_id)
            if record is None:
                raise ValueError(f"Unknown session: {session_id}")

            tools = self._create_tools(record.approval_mode)
            prompt = build_system_prompt(
                self.workspace,
                config=self.config,
                tool_names=[tool.definition.name for tool in tools],
                skills=self.skills,
                mode=record.approval_mode,
                global_memory_root=self.global_memory_root,
            )
            context = Context(system_prompt=prompt.system_prompt)
            events = await self.store.load_committed(session_id)
            context.messages.extend(_messages_from_committed_events(events))
            seed_history = getattr(self.event_sink, "seed_committed", None)
            if callable(seed_history):
                seed_history(events)
            self._session = await Session.load(
                self.store,
                session_id,
                context=context,
                sink=self.event_sink,
                frames=StreamFrameBuffer(),
            )
            return self._session

    def _create_tools(
        self,
        mode: ApprovalMode = ApprovalMode.MANUAL,
    ):
        tools = [
            *create_file_tools(
                self.workspace,
                extra_read_roots=self._skill_read_roots,
            ),
            *create_search_tools(
                self.workspace,
                embedder=self.embedder,
            ),
            *create_mutation_tools(self.workspace),
            *create_command_tools(self.workspace, self.config),
            *create_document_tools(self.workspace, self.config),
            *create_question_tools(self.questions),
            *create_skill_tools(
                self.skills,
                max_body_chars=self.config.max_tool_result_chars,
            ),
            *create_plan_tools(self.plans),
            *create_subagent_tools(self.subagents),
            *create_memory_tools(self.memory_store),
        ]
        if self.browser_bridge is not None:
            tools.extend(
                create_browser_tools(
                    self.browser_bridge,
                    self.config,
                )
            )
        if self.web_client is not None:
            tools.extend(
                create_web_tools(
                    self.web_client,
                    self.config,
                )
            )
        if mode is ApprovalMode.PLAN:
            allowed = {
                "read_file",
                "list_files",
                "glob",
                "grep",
                "search_codebase",
                "extract_document_text",
                "load_skill",
                "ask_user",
                "create_plan",
                "update_plan",
                "todo_write",
                "spawn_subagent",
                "browser_bridge",
                "web_search",
                "web_fetch",
                "memory_list",
            }
            return [
                tool
                for tool in tools
                if tool.definition.name in allowed
            ]
        return [
            tool
            for tool in tools
            if tool.definition.name not in {"create_plan", "update_plan"}
        ]

    def _create_hooks(self, session: Session) -> HookRegistry:
        hooks = HookRegistry()
        hooks.register(
            PostToolUse,
            create_spill_hook(
                self.workspace,
                session_id=session.session_id,
                config=self.config,
                exempt_tools=frozenset({"load_skill"}),
            ),
        )
        session.register_hooks(hooks)
        hooks.register(
            PreToolUse,
            create_approval_hook(
                SessionApprovalHandler(session, self.approvals),
                self.workspace,
                mode=lambda: session.approval_mode,
                manager=session.approvals,
                timeout_seconds=self.config.approval_timeout_seconds,
            ),
        )
        return hooks

    def _prepare_context(self, session: Session):
        async def prepare(context: Context) -> Context:
            return await self._compact_context(session, context)

        return prepare

    async def _compact_context(
        self,
        session: Session,
        context: Context,
    ) -> Context:
        config = self.config.compaction
        if not config.enabled:
            return context

        rows = rows_from_messages(context.messages)
        active = session.active_compaction
        summarize = self.summarize_fn or self._summarize_with_stream
        result = await maybe_compact(
            rows,
            active,
            config,
            summarize,
        )
        if result is not None:
            await session.commit_compaction(result.record)
            active = result.record
            summary_message = result.summary_message
        elif active is not None:
            summary_message = _summary_message_for_record(active)
        else:
            return context

        return Context(
            messages=project_model_messages(
                context.messages,
                active,
                summary_message,
            ),
            system_prompt=context.system_prompt,
            tools=list(context.tools),
        )

    async def _summarize_with_stream(
        self,
        folded: Sequence[CompactionRow],
        prior_summary: str,
    ) -> str:
        messages = build_summarizer_messages(
            folded,
            prior_summary,
            self.config.compaction,
        )
        if not messages:
            return ""

        system_prompt = (
            messages[0].content
            if messages[0].role == "system"
            else summary_instructions
        )
        timestamp = _now_ms()
        context_messages: list[Message] = []
        for message in messages[1:]:
            if message.role == "user":
                context_messages.append(
                    UserMessage(
                        content=message.content,
                        timestamp=timestamp,
                    )
                )
            elif message.role == "assistant":
                context_messages.append(
                    AssistantMessage(
                        content=[TextContent(message.content)],
                        provider=self.model.provider,
                        model=self.model.id,
                        usage=Usage(),
                        stop_reason="stop",
                        timestamp=timestamp,
                    )
                )

        context = Context(
            system_prompt=system_prompt,
            messages=context_messages,
        )
        try:
            response = await asyncio.wait_for(
                self._run_summary_stream(context),
                timeout=self.config.compaction.summarizer_timeout_seconds,
            )
        except Exception:
            return ""

        if response.stop_reason in ("error", "aborted"):
            return ""
        if any(
            isinstance(block, ToolCall)
            for block in response.content
        ):
            return ""
        return "\n".join(
            block.text
            for block in response.content
            if isinstance(block, TextContent)
        ).strip()

    async def _run_summary_stream(
        self,
        context: Context,
    ) -> AssistantMessage:
        stream = self.stream_fn(
            self.model,
            context,
            StreamOptions(
                max_tokens=self.config.compaction.summarizer_max_tokens,
                cancel_event=self._cancel_event,
            ),
        )
        async for _ in stream:
            pass
        return await stream.result()

    async def prompt(
        self,
        text: str,
        *,
        attachments: Sequence[UserAttachment] = (),
        selections: Sequence[UserSelection] = (),
        active_paths: Sequence[str] = (),
        reasoning: str | None = None,
        display_text: str | None = None,
    ) -> AssistantMessage:
        attachment_refs = tuple(attachments)
        selection_refs = tuple(selections)
        if not text.strip() and not attachment_refs and not selection_refs:
            raise ValueError("prompt must not be empty")
        if len(attachment_refs) > MAX_USER_ATTACHMENTS:
            raise ValueError(
                f"At most {MAX_USER_ATTACHMENTS} attachments are allowed"
            )
        if len(selection_refs) > MAX_USER_SELECTIONS:
            raise ValueError(
                f"At most {MAX_USER_SELECTIONS} selections are allowed"
            )
        if reasoning not in (
            None,
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError(
                "reasoning must be one of: low, medium, high, xhigh, max"
            )

        async with self._prompt_lock:
            session = await self._ensure_session()
            self._cancel_event = asyncio.Event()

            content: str | list[TextContent | ImageContent] = text
            if attachment_refs:
                image_blocks = [
                    self.workspace.read_image(ref.relative_path)
                    for ref in attachment_refs
                    if ref.kind == "image"
                ]
                file_notes: list[str] = []
                for ref in attachment_refs:
                    if ref.kind != "file":
                        continue
                    candidate = Path(ref.relative_path)
                    resolved = (
                        candidate
                        if candidate.is_absolute()
                        else self.workspace.resolve(candidate)
                    )
                    if not resolved.is_file():
                        raise ValueError(
                            f"Attachment does not exist: {ref.relative_path}"
                        )
                    if resolved.stat().st_size > MAX_ATTACHMENT_FILE_BYTES:
                        raise ValueError(
                            "Attachment exceeds "
                            f"{MAX_ATTACHMENT_FILE_BYTES} bytes: "
                            f"{ref.relative_path}"
                        )
                    file_notes.append(
                        f"- {resolved} ({ref.media_type})"
                    )

                text_parts: list[str] = []
                if text.strip():
                    text_parts.append(text)
                if file_notes:
                    text_parts.append(
                        "Attached files (use extract_document_text for "
                        "PDF/DOCX/PPTX, or other tools/commands as needed):\n"
                        + "\n".join(file_notes)
                    )
                if selection_refs:
                    text_parts.append(_selection_context(selection_refs))

                blocks: list[TextContent | ImageContent] = [
                    *image_blocks
                ]
                if text_parts:
                    blocks.append(TextContent("\n\n".join(text_parts)))
                if blocks:
                    content = blocks
            elif selection_refs:
                text_parts = []
                if text.strip():
                    text_parts.append(text)
                text_parts.append(_selection_context(selection_refs))
                content = [TextContent("\n\n".join(text_parts))]

            user_message = UserMessage(content=content, timestamp=_now_ms())
            await session.start_run(
                user_message,
                attachments=attachment_refs,
                selections=selection_refs,
                display_text=(
                    display_text
                    if display_text is not None
                    else text
                ),
            )
            return await self._run_agent(
                session,
                reasoning,
                has_attachments=bool(attachment_refs),
                active_paths=tuple(
                    dict.fromkeys(
                        [
                            *active_paths,
                            *(
                                selection.path
                                for selection in selection_refs
                                if selection.path
                            ),
                            *(
                                ref.relative_path
                                for ref in attachment_refs
                            ),
                        ]
                    )
                ),
            )

    async def regenerate(
        self,
        *,
        reasoning: str | None = None,
    ) -> AssistantMessage:
        if reasoning not in (
            None,
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError(
                "reasoning must be one of: low, medium, high, xhigh, max"
            )
        async with self._prompt_lock:
            session = await self._ensure_session()
            if not session.context.messages:
                raise ValueError("Nothing to regenerate")
            self._cancel_event = asyncio.Event()
            await session.start_continuation_run()
            return await self._run_agent(session, reasoning)

    async def _run_agent(
        self,
        session: Session,
        reasoning: str | None,
        *,
        has_attachments: bool = False,
        active_paths: tuple[str, ...] = (),
    ) -> AssistantMessage:
        mode = session.approval_mode
        self.plans.bind(
            session_id=session.session_id,
            run_id=session.run_id,
        )
        tools = self._create_tools(mode)
        prompt = build_system_prompt(
            self.workspace,
            config=self.config,
            tool_names=[tool.definition.name for tool in tools],
            skills=self.skills,
            mode=mode,
            global_memory_root=self.global_memory_root,
            active_paths=active_paths,
        )
        session.context.system_prompt = prompt.system_prompt
        hooks = self._create_hooks(session)
        agent = Agent(
            model=self.model,
            stream_fn=self.stream_fn,
            context=session.context,
            tools=tools,
            hooks=hooks,
            options=StreamOptions(
                cancel_event=self._cancel_event,
                reasoning=reasoning,
            ),
            max_steps=self.config.agent_max_steps,
            max_parallel_tools=self.config.agent_max_parallel_tools,
            prepare_context=self._prepare_context(session),
        )
        try:
            result = await agent.run(
                None,
                on_event=session.handle_model_event,
            )
        except AgentLoopLimitError as exc:
            await session.stop_run("max_steps", exc.last_message)
            raise
        except AgentInputBlocked:
            await session.stop_run("input_blocked")
            raise
        finally:
            if self.questions is not None:
                await self.questions.cancel_all("Run finished")

        if result.stop_reason == "error" and has_attachments:
            result.error_message = friendly_provider_error(
                result.error_message
            )

        status = (
            "aborted"
            if result.stop_reason == "aborted"
            else "error"
            if result.stop_reason == "error"
            else "completed"
        )
        await session.stop_run(status, result)
        return result

    async def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._session is not None:
            self._session.approvals.cancel_all("Run cancelled")
        if self.questions is not None:
            await self.questions.cancel_all("Run cancelled")

    async def set_approval_mode(self, mode: ApprovalMode) -> None:
        session = await self._ensure_session()
        await session.set_approval_mode(mode)

    async def get_approval_mode(self) -> ApprovalMode:
        session = await self._ensure_session()
        return session.approval_mode

    async def timeline_snapshot(self) -> TimelineSnapshot | None:
        session = self._session
        if session is None:
            return None
        projector = TimelineProjector(session.session_id)
        events = await self.store.load_committed(session.session_id)
        for envelope in events:
            projector.push_committed(envelope)
        if session.run_id is not None:
            for frame in session.frames.snapshot(session.run_id):
                projector.push_frame(frame)
        return projector.snapshot()

    async def close(self) -> None:
        if self._closed:
            return
        await self.cancel()
        async with self._prompt_lock:
            self._closed = True
        await self.store.close()

    async def __aenter__(self) -> "CodingAgent":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


__all__ = ["CodingAgent"]


def _summary_message_for_record(record: CompactionRecord) -> UserMessage:
    return UserMessage(
        content=wrap_summary(record.id, record.summary),
        timestamp=_now_ms(),
    )


def _messages_from_committed_events(events) -> list[Message]:
    messages: list[Message] = []
    for envelope in events:
        event = envelope.event
        if isinstance(
            event,
            (UserMessageCommitted, AssistantMessageCommitted),
        ):
            messages.append(event.message)
        elif isinstance(event, ToolResultCommitted):
            messages.append(event.result)
    return messages

"""Tests for ling_agent approvals and mutating tools."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ling_ai.events import DoneEvent, StartEvent
from ling_ai.stream import AssistantMessageEventStream
from ling_ai.types import (
    AssistantContent,
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)
from ling_agent import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalRequest,
    Workspace,
    create_approval_hook,
    create_mutation_tools,
)
from ling_agent.local_settings import LocalSettingsStore
from ling_core import Agent, HookRegistry, PreToolUse, ToolContext

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


class StaticApprovalHandler:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decision


def _pre_tool(
    name: str,
    arguments: dict[str, object] | None = None,
    *,
    cancel_event: asyncio.Event | None = None,
) -> PreToolUse:
    return PreToolUse(
        call=ToolCall(
            id="call_1",
            name=name,
            arguments=dict(arguments or {}),
        ),
        context=ToolContext(step=1, cancel_event=cancel_event),
    )


async def _approval_contracts() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        readonly_hook = create_approval_hook(None, root, timeout_seconds=0.1)
        assert await readonly_hook(_pre_tool("read_file", {"path": "a.txt"})) is None

        allow_handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)
        allow_hook = create_approval_hook(allow_handler, root, timeout_seconds=0.1)
        allowed = await allow_hook(_pre_tool("write_file", {"path": "a.txt"}))
        assert allowed is not None and allowed.allowed
        assert allow_handler.requests[0].kind is ApprovalKind.WRITE

        deny_handler = StaticApprovalHandler(ApprovalDecision.DENY)
        deny_hook = create_approval_hook(deny_handler, root, timeout_seconds=0.1)
        denied = await deny_hook(_pre_tool("edit_file", {"path": "a.txt"}))
        assert denied is not None and not denied.allowed

        no_handler = create_approval_hook(None, root, timeout_seconds=0.1)
        missing = await no_handler(_pre_tool("write_file", {"path": "a.txt"}))
        assert missing is not None and not missing.allowed

        class RaisingHandler:
            async def request(self, request: ApprovalRequest) -> ApprovalDecision:
                raise RuntimeError("approval broke")

        failed = await create_approval_hook(
            RaisingHandler(), root, timeout_seconds=0.1
        )(_pre_tool("write_file", {"path": "a.txt"}))
        assert failed is not None and "approval broke" in (failed.reason or "")

        class SlowHandler:
            async def request(self, request: ApprovalRequest) -> ApprovalDecision:
                await asyncio.sleep(1)
                return ApprovalDecision.ALLOW_ONCE

        timed_out = await create_approval_hook(
            SlowHandler(), root, timeout_seconds=0.01
        )(_pre_tool("write_file", {"path": "a.txt"}))
        assert timed_out is not None and not timed_out.allowed

        cancel = asyncio.Event()

        class CancellingHandler:
            async def request(self, request: ApprovalRequest) -> ApprovalDecision:
                cancel.set()
                return ApprovalDecision.ALLOW_ONCE

        cancelled = await create_approval_hook(
            CancellingHandler(), root, timeout_seconds=0.1
        )(_pre_tool("write_file", {"path": "a.txt"}, cancel_event=cancel))
        assert cancelled is not None and not cancelled.allowed


def test_approval_contracts() -> None:
    asyncio.run(_approval_contracts())


def _assistant(
    content: list[AssistantContent],
    *,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
    )


class ScriptedStreams:
    def __init__(self, messages: list[AssistantMessage]) -> None:
        self.messages = list(messages)

    def __call__(
        self,
        model: Model,
        context: Context,
        options: object,
    ) -> AssistantMessageEventStream:
        message = self.messages.pop(0)
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=message))
        stream.push(DoneEvent(reason=message.stop_reason, message=message))
        stream.end()
        return stream


def test_allowed_mutations_are_applied_in_order() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = Workspace(root)
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)
        hooks = HookRegistry()
        hooks.register(
            PreToolUse,
            create_approval_hook(handler, root, timeout_seconds=0.1),
        )
        streams = ScriptedStreams(
            [
                _assistant(
                    [
                        ToolCall(
                            id="call_1",
                            name="write_file",
                            arguments={"path": "a.txt", "content": "hello"},
                        ),
                        ToolCall(
                            id="call_2",
                            name="edit_file",
                            arguments={
                                "path": "a.txt",
                                "old_text": "hello",
                                "new_text": "goodbye",
                            },
                        ),
                    ],
                    stop_reason="tool_use",
                ),
                _assistant([TextContent("done")]),
            ]
        )
        agent = Agent(
            model=MODEL,
            stream_fn=streams,
            hooks=hooks,
            tools=create_mutation_tools(workspace),
        )

        result = asyncio.run(agent.run("write and edit"))

        assert result.content == [TextContent("done")]
        assert (root / "a.txt").read_text(encoding="utf-8") == "goodbye"
        assert [request.kind for request in handler.requests] == [
            ApprovalKind.WRITE,
            ApprovalKind.EDIT,
        ]


def test_denied_write_never_touches_disk() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        workspace = Workspace(root)
        handler = StaticApprovalHandler(ApprovalDecision.DENY)
        hooks = HookRegistry()
        hooks.register(
            PreToolUse,
            create_approval_hook(handler, root, timeout_seconds=0.1),
        )
        streams = ScriptedStreams(
            [
                _assistant(
                    [
                        ToolCall(
                            id="call_1",
                            name="write_file",
                            arguments={"path": "blocked.txt", "content": "secret"},
                        )
                    ],
                    stop_reason="tool_use",
                ),
                _assistant([TextContent("recovered")]),
            ]
        )
        agent = Agent(
            model=MODEL,
            stream_fn=streams,
            hooks=hooks,
            tools=create_mutation_tools(workspace),
        )

        result = asyncio.run(agent.run("write blocked file"))

        assert result.content == [TextContent("recovered")]
        assert not (root / "blocked.txt").exists()
        tool_result = next(
            message
            for message in agent.context.messages
            if isinstance(message, ToolResultMessage)
        )
        assert tool_result.is_error
        assert "Permission denied" in tool_result.content[0].text


class FeedbackApprovalHandler:
    async def request(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        return ApprovalDecision.DENY

    def feedback_for(self, approval_id: str) -> str | None:
        return "use pnpm instead"


def test_approval_feedback_reaches_denial_reason() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        hook = create_approval_hook(FeedbackApprovalHandler(), root)

        decision = asyncio.run(
            hook(_pre_tool("run_command", {"command": "npm install"}))
        )

        assert decision is not None
        assert decision.allowed is False
        assert "use pnpm instead" in (decision.reason or "")


def test_local_allowlist_skips_matching_normal_command() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = LocalSettingsStore(root)
        store.allow_command("npm:*")
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)
        hook = create_approval_hook(
            handler,
            root,
            local_settings=store,
        )

        decision = asyncio.run(
            hook(_pre_tool("run_command", {"command": "npm install"}))
        )

        assert decision is not None
        assert decision.allowed is True
        assert handler.requests == []


def test_local_allowlist_never_bypasses_destructive_command() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = LocalSettingsStore(root)
        store.allow_command("Remove-Item:*")
        handler = StaticApprovalHandler(ApprovalDecision.ALLOW_ONCE)
        hook = create_approval_hook(
            handler,
            root,
            local_settings=store,
        )

        decision = asyncio.run(
            hook(
                _pre_tool(
                    "run_command",
                    {"command": "Remove-Item -Recurse -Force build"},
                )
            )
        )

        assert decision is not None
        assert decision.allowed is True
        assert [request.kind for request in handler.requests] == [
            ApprovalKind.COMMAND,
        ]


if __name__ == "__main__":
    test_approval_contracts()
    test_allowed_mutations_are_applied_in_order()
    test_denied_write_never_touches_disk()
    test_local_allowlist_skips_matching_normal_command()
    test_local_allowlist_never_bypasses_destructive_command()
    print("all ling_agent approval tests passed")

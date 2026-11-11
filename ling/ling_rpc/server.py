"""Newline-delimited JSON-RPC server used by the Ling sidecar."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, TextIO

from ling_agent import (
    MAX_SELECTION_CHARS,
    MAX_USER_ATTACHMENTS,
    MAX_USER_SELECTIONS,
    ApprovalMode,
    UserAttachment,
    UserSelection,
)
from ling_comment.targets import CommentError, CommentTarget

from .comments import CommentService
from .sessions import SessionService
from .ui_events import UiEvent

PROTOCOL_VERSION = 1
SERVER_NAME = "ling-sidecar"
SERVER_VERSION = "0.1.0"
MAX_COMMENT_SOURCE_CHARS = 200_000


class JsonRpcError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        data: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class JsonRpcServer:
    """Minimal server with stdout reserved for JSON-RPC messages."""

    def __init__(
        self,
        stdout: TextIO,
        stderr: TextIO,
        *,
        sessions: SessionService | None = None,
        comments: CommentService | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._write_lock = asyncio.Lock()
        self._sessions = sessions or SessionService(
            self._emit_ui_event,
            emit_index_progress=self._emit_index_progress,
        )
        self._comments = comments or CommentService()
        self.running = True

    async def serve(self, stdin: TextIO) -> None:
        try:
            while self.running:
                raw_line = await asyncio.to_thread(stdin.readline)
                if not raw_line:
                    break
                if not raw_line.strip():
                    continue

                response = await self.handle_line(raw_line)
                if response is not None:
                    await self._write_message(response)
        finally:
            await self._sessions.close()

    async def handle_line(
        self,
        raw_line: str,
    ) -> dict[str, Any] | None:
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            return error_response(
                None,
                -32700,
                "Parse error",
                {"detail": str(exc)},
            )

        if isinstance(message, list):
            return error_response(
                None,
                -32600,
                "Invalid Request",
                {"detail": "Batch requests are not supported"},
            )

        return await self.handle_message(message)

    async def handle_message(
        self,
        message: object,
    ) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return error_response(None, -32600, "Invalid Request")

        request_id = message.get("id")
        is_notification = "id" not in message
        if message.get("jsonrpc") != "2.0":
            return (
                None
                if is_notification
                else error_response(request_id, -32600, "Invalid Request")
            )

        method = message.get("method")
        if not isinstance(method, str):
            return (
                None
                if is_notification
                else error_response(request_id, -32600, "Invalid Request")
            )

        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return (
                None
                if is_notification
                else error_response(request_id, -32602, "Invalid params")
            )

        try:
            result = await self._dispatch(method, params)
        except JsonRpcError as exc:
            if is_notification:
                return None
            return error_response(
                request_id,
                exc.code,
                exc.message,
                exc.data,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            print(
                f"Unhandled JSON-RPC error in {method}: {exc}",
                file=self._stderr,
                flush=True,
            )
            if is_notification:
                return None
            return error_response(request_id, -32603, "Internal error")

        if is_notification:
            return None
        return success_response(request_id, result)

    async def _dispatch(
        self,
        method: str,
        params: dict[str, Any],
    ) -> object:
        if method == "initialize":
            protocol_version = params.get("protocolVersion")
            if protocol_version != PROTOCOL_VERSION:
                raise JsonRpcError(
                    -32602,
                    "Unsupported protocol version",
                    {
                        "requested": protocol_version,
                        "supported": PROTOCOL_VERSION,
                    },
                )

            return {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "capabilities": {
                    "ping": True,
                    "sessions": True,
                    "comments": True,
                },
            }

        if method == "ping":
            nonce = params.get("nonce")
            if nonce is not None and not isinstance(nonce, str):
                raise JsonRpcError(-32602, "nonce must be a string")
            return {"pong": True, "nonce": nonce}

        if method == "shutdown":
            self.running = False
            return None

        if method == "comment.generate":
            target = _parse_comment_target(params)
            try:
                return await self._comments.generate(target)
            except CommentError as exc:
                raise JsonRpcError(
                    -32001,
                    str(exc),
                    {"reason": exc.reason},
                ) from exc
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32001, str(exc)) from exc

        if method == "session.start":
            prompt = params.get("prompt")
            workspace_root = params.get("workspaceRoot")
            approval_mode = _parse_approval_mode(
                params.get("approvalMode"),
                required=False,
            )
            attachments = _parse_user_attachments(params.get("attachments"))
            selections = _parse_user_selections(params.get("selections"))
            reasoning = params.get("reasoning")
            if reasoning in (None, "", "auto"):
                reasoning = None
            elif reasoning not in (
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ):
                raise JsonRpcError(
                    -32602,
                    "reasoning must be one of: auto, low, medium, high, "
                    "xhigh, max",
                )
            if not isinstance(prompt, str):
                raise JsonRpcError(-32602, "prompt must be a string")
            if not prompt.strip() and not attachments and not selections:
                raise JsonRpcError(
                    -32602,
                    "prompt, attachments, or selections must be provided",
                )
            if not isinstance(workspace_root, str) or not workspace_root:
                raise JsonRpcError(
                    -32602,
                    "workspaceRoot must be a non-empty string",
                )
            try:
                return await self._sessions.start(
                    prompt,
                    workspace_root,
                    approval_mode=approval_mode,
                    attachments=attachments,
                    selections=selections,
                    reasoning=reasoning,
                )
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32001, str(exc)) from exc

        if method == "session.deleteMessage":
            message_id = params.get("messageId")
            if not isinstance(message_id, str) or not message_id:
                raise JsonRpcError(
                    -32602,
                    "messageId must be a non-empty string",
                )
            try:
                return await self._sessions.delete_message(message_id)
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32001, str(exc)) from exc

        if method == "session.regenerate":
            message_id = params.get("messageId")
            if not isinstance(message_id, str) or not message_id:
                raise JsonRpcError(
                    -32602,
                    "messageId must be a non-empty string",
                )
            reasoning = params.get("reasoning")
            if reasoning in (None, "", "auto"):
                reasoning = None
            elif reasoning not in (
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ):
                raise JsonRpcError(
                    -32602,
                    "reasoning must be one of: auto, low, medium, high, "
                    "xhigh, max",
                )
            try:
                return await self._sessions.regenerate_message(
                    message_id,
                    reasoning=reasoning,
                )
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32001, str(exc)) from exc

        if method == "session.cancel":
            await self._sessions.cancel()
            return {"cancelled": True}

        if method == "session.queued.sendNow":
            queued_id = params.get("queuedId")
            if not isinstance(queued_id, str) or not queued_id:
                raise JsonRpcError(
                    -32602,
                    "queuedId must be a non-empty string",
                )
            return {
                "accepted": await self._sessions.send_queued_now(queued_id)
            }

        if method == "session.new":
            await self._sessions.new_session()
            return {"accepted": True}

        if method == "session.snapshot":
            return await self._sessions.snapshot()

        if method == "session.setMode":
            mode = _parse_approval_mode(
                params.get("mode"),
                required=True,
            )
            assert mode is not None
            applied = await self._sessions.set_approval_mode(mode)
            return {"mode": applied.value}

        if method == "session.list":
            workspace_root = params.get("workspaceRoot")
            if workspace_root is not None and (
                not isinstance(workspace_root, str)
                or not workspace_root.strip()
            ):
                raise JsonRpcError(
                    -32602,
                    "workspaceRoot must be a non-empty string",
                )
            return {
                "sessions": await self._sessions.list_sessions(
                    workspace_root
                )
            }

        if method == "session.search":
            query = params.get("query")
            if not isinstance(query, str):
                raise JsonRpcError(-32602, "query must be a string")
            workspace_root = params.get("workspaceRoot")
            if workspace_root is not None and (
                not isinstance(workspace_root, str)
                or not workspace_root.strip()
            ):
                raise JsonRpcError(
                    -32602,
                    "workspaceRoot must be a non-empty string",
                )
            limit = params.get("limit", 50)
            if (
                not isinstance(limit, int)
                or isinstance(limit, bool)
                or limit < 1
                or limit > 100
            ):
                raise JsonRpcError(
                    -32602,
                    "limit must be an integer between 1 and 100",
                )
            return {
                "sessions": await self._sessions.search_sessions(
                    query,
                    workspace_root=workspace_root,
                    limit=limit,
                )
            }

        if method == "index.build":
            workspace_root = params.get("workspaceRoot")
            if not isinstance(workspace_root, str) or not (
                workspace_root.strip()
            ):
                raise JsonRpcError(
                    -32602,
                    "workspaceRoot must be a non-empty string",
                )
            try:
                return await self._sessions.build_code_index(
                    workspace_root
                )
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32001, str(exc)) from exc

        if method == "index.status":
            workspace_root = params.get("workspaceRoot")
            if not isinstance(workspace_root, str) or not (
                workspace_root.strip()
            ):
                raise JsonRpcError(
                    -32602,
                    "workspaceRoot must be a non-empty string",
                )
            try:
                return await self._sessions.code_index_status(
                    workspace_root
                )
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32001, str(exc)) from exc

        if method == "session.delete":
            session_id = params.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise JsonRpcError(
                    -32602,
                    "sessionId must be a string",
                )
            try:
                await self._sessions.delete_session(session_id)
            except ValueError as exc:
                raise JsonRpcError(-32004, str(exc)) from exc
            except RuntimeError as exc:
                raise JsonRpcError(-32001, str(exc)) from exc
            return {"deleted": True}

        if method == "session.history":
            session_id = params.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise JsonRpcError(-32602, "sessionId must be a string")
            try:
                return await self._sessions.history_snapshot(session_id)
            except ValueError as exc:
                raise JsonRpcError(-32004, str(exc)) from exc

        if method == "session.open":
            session_id = params.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise JsonRpcError(-32602, "sessionId must be a string")
            workspace_root = params.get("workspaceRoot")
            if workspace_root is not None and (
                not isinstance(workspace_root, str)
                or not workspace_root.strip()
            ):
                raise JsonRpcError(
                    -32602,
                    "workspaceRoot must be a non-empty string",
                )
            try:
                return await self._sessions.open_session(
                    session_id,
                    workspace_root=workspace_root,
                )
            except (RuntimeError, ValueError) as exc:
                raise JsonRpcError(-32004, str(exc)) from exc

        if method == "approval.resolve":
            approval_id = params.get("approvalId")
            effect_digest = params.get("effectDigest")
            approved = params.get("approved")
            if not isinstance(approval_id, str) or not approval_id:
                raise JsonRpcError(-32602, "approvalId must be a string")
            if not isinstance(effect_digest, str) or not effect_digest:
                raise JsonRpcError(-32602, "effectDigest must be a string")
            if not isinstance(approved, bool):
                raise JsonRpcError(-32602, "approved must be a boolean")
            return {
                "resolved": await self._sessions.resolve_approval(
                    approval_id,
                    effect_digest,
                    approved,
                )
            }

        if method == "question.answer":
            call_id = params.get("callId")
            if not isinstance(call_id, str) or not call_id:
                raise JsonRpcError(-32602, "callId must be a string")
            cancelled = params.get("cancelled", False)
            if not isinstance(cancelled, bool):
                raise JsonRpcError(-32602, "cancelled must be a boolean")
            answers = params.get("answers")
            if not cancelled and not isinstance(answers, list):
                raise JsonRpcError(-32602, "answers must be an array")
            try:
                resolved = await self._sessions.resolve_question(
                    call_id,
                    answers,
                    cancelled=cancelled,
                )
            except ValueError as exc:
                raise JsonRpcError(-32602, str(exc)) from exc
            return {"resolved": resolved}

        raise JsonRpcError(-32601, "Method not found", {"method": method})

    async def _emit_ui_event(self, event: UiEvent) -> None:
        await self._write_message(
            {
                "jsonrpc": "2.0",
                "method": "ui.event",
                "params": {"event": event},
            }
        )

    async def _emit_index_progress(
        self,
        stats: dict[str, Any],
    ) -> None:
        await self._write_message(
            {
                "jsonrpc": "2.0",
                "method": "index.progress",
                "params": stats,
            }
        )

    async def _write_message(self, message: dict[str, Any]) -> None:
        async with self._write_lock:
            self._stdout.write(
                json.dumps(message, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            self._stdout.flush()


def _parse_user_selections(value: object) -> tuple[UserSelection, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise JsonRpcError(-32602, "selections must be an array")
    if len(value) > MAX_USER_SELECTIONS:
        raise JsonRpcError(
            -32602,
            f"selections must contain at most {MAX_USER_SELECTIONS} items",
        )

    selections: list[UserSelection] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise JsonRpcError(
                -32602,
                f"selections[{index}] must be an object",
            )
        kind = item.get("kind")
        label = item.get("label")
        text = item.get("text")
        line_count = item.get("lineCount")
        path = item.get("path")
        start_line = item.get("startLine")
        end_line = item.get("endLine")
        if kind not in ("editor", "terminal", "chat"):
            raise JsonRpcError(
                -32602,
                f"selections[{index}].kind is invalid",
            )
        if not isinstance(label, str) or not label.strip():
            raise JsonRpcError(
                -32602,
                f"selections[{index}].label must be a string",
            )
        if not isinstance(text, str) or not text.strip():
            raise JsonRpcError(
                -32602,
                f"selections[{index}].text must be a string",
            )
        if len(text) > MAX_SELECTION_CHARS:
            raise JsonRpcError(
                -32602,
                f"selections[{index}].text is too long",
            )
        if (
            not isinstance(line_count, int)
            or isinstance(line_count, bool)
            or line_count < 1
        ):
            raise JsonRpcError(
                -32602,
                f"selections[{index}].lineCount must be a positive integer",
            )
        if path is not None and not isinstance(path, str):
            raise JsonRpcError(
                -32602,
                f"selections[{index}].path must be a string",
            )
        for name, number in (
            ("startLine", start_line),
            ("endLine", end_line),
        ):
            if number is not None and (
                not isinstance(number, int)
                or isinstance(number, bool)
                or number < 1
            ):
                raise JsonRpcError(
                    -32602,
                    f"selections[{index}].{name} must be a positive integer",
                )
        selections.append(
            UserSelection(
                kind=kind,
                label=label,
                text=text,
                line_count=line_count,
                path=path,
                start_line=start_line,
                end_line=end_line,
            )
        )
    return tuple(selections)


def _parse_user_attachments(value: object) -> tuple[UserAttachment, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise JsonRpcError(-32602, "attachments must be an array")
    if len(value) > MAX_USER_ATTACHMENTS:
        raise JsonRpcError(
            -32602,
            f"attachments must contain at most {MAX_USER_ATTACHMENTS} items",
        )

    attachments: list[UserAttachment] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise JsonRpcError(
                -32602,
                f"attachments[{index}] must be an object",
            )
        relative_path = item.get("relativePath")
        name = item.get("name")
        media_type = item.get("mediaType")
        kind = item.get("kind", "image")
        if kind not in ("image", "file"):
            raise JsonRpcError(
                -32602,
                f"attachments[{index}].kind must be 'image' or 'file'",
            )
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise JsonRpcError(
                -32602,
                f"attachments[{index}].relativePath must be a string",
            )
        if not isinstance(name, str) or not name.strip():
            raise JsonRpcError(
                -32602,
                f"attachments[{index}].name must be a string",
            )
        if not isinstance(media_type, str) or not media_type.strip():
            raise JsonRpcError(
                -32602,
                f"attachments[{index}].mediaType must be a string",
            )
        try:
            attachments.append(
                UserAttachment(
                    relative_path=relative_path,
                    name=name,
                    media_type=media_type,
                    kind=kind,
                )
            )
        except ValueError as exc:
            raise JsonRpcError(
                -32602,
                f"Invalid attachments[{index}]: {exc}",
            ) from exc
    return tuple(attachments)


def _parse_comment_target(params: dict[str, Any]) -> CommentTarget:
    path = params.get("path")
    if not isinstance(path, str) or not path.strip():
        raise JsonRpcError(
            -32602,
            "path must be a non-empty string",
        )

    language_id = params.get("languageId")
    if not isinstance(language_id, str) or not language_id.strip():
        raise JsonRpcError(
            -32602,
            "languageId must be a non-empty string",
        )

    symbol_name = params.get("symbolName")
    if not isinstance(symbol_name, str) or not symbol_name.strip():
        raise JsonRpcError(
            -32602,
            "symbolName must be a non-empty string",
        )

    symbol_kind = params.get("symbolKind")
    if symbol_kind not in ("function", "method", "class"):
        raise JsonRpcError(
            -32602,
            "symbolKind must be one of: function, method, class",
        )

    start_line = params.get("startLine")
    if (
        not isinstance(start_line, int)
        or isinstance(start_line, bool)
        or start_line < 0
    ):
        raise JsonRpcError(
            -32602,
            "startLine must be a non-negative integer",
        )

    source_text = params.get("sourceText")
    if not isinstance(source_text, str) or not source_text.strip():
        raise JsonRpcError(
            -32602,
            "sourceText must be a non-empty string",
        )
    if len(source_text) > MAX_COMMENT_SOURCE_CHARS:
        raise JsonRpcError(
            -32602,
            f"sourceText must be at most {MAX_COMMENT_SOURCE_CHARS} "
            "characters",
        )

    document_version = params.get("documentVersion", 0)
    if (
        not isinstance(document_version, int)
        or isinstance(document_version, bool)
        or document_version < 0
    ):
        raise JsonRpcError(
            -32602,
            "documentVersion must be a non-negative integer",
        )

    content_hash = params.get("contentHash", "")
    if not isinstance(content_hash, str):
        raise JsonRpcError(
            -32602,
            "contentHash must be a string",
        )

    class_context = params.get("classContext")
    if class_context is not None and not isinstance(class_context, str):
        raise JsonRpcError(
            -32602,
            "classContext must be a string",
        )

    return CommentTarget(
        path=path,
        language_id=language_id,
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        start_line=start_line,
        source_text=source_text,
        document_version=document_version,
        content_hash=content_hash,
        class_context=class_context,
    )


def _parse_approval_mode(
    value: object,
    *,
    required: bool,
) -> ApprovalMode | None:
    if value is None:
        if required:
            raise JsonRpcError(-32602, "mode must be a string")
        return None
    if not isinstance(value, str):
        raise JsonRpcError(-32602, "approval mode must be a string")
    try:
        return ApprovalMode(value)
    except ValueError as exc:
        raise JsonRpcError(
            -32602,
            f"Unsupported approval mode: {value}",
        ) from exc


def success_response(request_id: object, result: object) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def error_response(
    request_id: object,
    code: int,
    message: str,
    data: object | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    server = JsonRpcServer(stdout=sys.stdout, stderr=sys.stderr)
    asyncio.run(server.serve(sys.stdin))

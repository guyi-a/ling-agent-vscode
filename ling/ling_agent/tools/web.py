"""Web search and page-fetch tools backed by DeepSeek."""

from __future__ import annotations

import json
from typing import Any

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..config import HarnessConfig
from ..web import WebClient, WebClientError


def _string_arg(call: ToolCall, name: str) -> str:
    value = call.arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _int_arg(
    call: ToolCall,
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = call.arguments.get(name, default)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(
            f"{name} must be an integer between {minimum} and {maximum}"
        )
    return value


def _bool_arg(call: ToolCall, name: str, default: bool = False) -> bool:
    value = call.arguments.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _error_output(exc: Exception) -> str:
    code = getattr(exc, "code", "INVALID_ARGUMENT")
    retryable = bool(getattr(exc, "retryable", False))
    return json.dumps(
        {
            "ok": False,
            "code": code,
            "message": str(exc),
            **({"retryable": True} if retryable else {}),
        },
        ensure_ascii=False,
    )


def _execution(
    payload: dict[str, Any],
    *,
    target: str,
) -> ToolExecution:
    return ToolExecution(
        output=json.dumps(payload, ensure_ascii=False),
        details={
            "kind": "io",
            "input": target,
            "output": _summary(payload),
        },
    )


def _summary(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return str(payload.get("message", "Web request failed"))
    results = payload.get("results")
    if isinstance(results, list):
        if not results:
            return "No results"
        lines: list[str] = []
        for result in results[:5]:
            if not isinstance(result, dict):
                continue
            title = result.get("title")
            url = result.get("url")
            if isinstance(title, str) and isinstance(url, str):
                lines.append(f"{title}\n{url}")
        remaining = len(results) - 5
        if remaining > 0:
            lines.append(f"... {remaining} more")
        return "\n\n".join(lines)
    title = payload.get("title")
    content = payload.get("content")
    if isinstance(title, str) and isinstance(content, str):
        preview = content[:500]
        return f"{title}\n\n{preview}".strip()
    return "Completed"


def create_web_tools(
    client: WebClient,
    config: HarnessConfig,
) -> list[AgentTool]:
    async def web_search(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        try:
            query = _string_arg(call, "query")
            count = _int_arg(
                call,
                "count",
                default=5,
                minimum=1,
                maximum=10,
            )
            need_content = _bool_arg(call, "need_content")
            result = await client.search(
                query,
                count=count,
                need_content=need_content,
                cancel_event=context.cancel_event,
            )
            results = result.get("results", [])
            payload: dict[str, Any] = {
                "ok": True,
                "query": query,
                "results": results,
            }
            if not results:
                payload["message"] = "No web results matched the query."
            return _execution(
                payload,
                target=query,
            )
        except (WebClientError, ValueError) as exc:
            return ToolExecution(
                output=_error_output(exc),
                is_error=True,
                details={
                    "kind": "io",
                    "input": str(call.arguments.get("query", "")),
                    "error": str(exc),
                },
            )

    async def web_fetch(
        call: ToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        try:
            url = _string_arg(call, "url")
            result = await client.fetch(
                url,
                cancel_event=context.cancel_event,
            )
            content = result.get("content", "")
            if (
                isinstance(content, str)
                and len(content) > config.max_tool_result_chars
            ):
                result["content"] = (
                    content[: config.max_tool_result_chars]
                    + "\n\n[content truncated]"
                )
                result["truncated"] = True
            return _execution(
                {"ok": True, "url": url, **result},
                target=url,
            )
        except (WebClientError, ValueError) as exc:
            return ToolExecution(
                output=_error_output(exc),
                is_error=True,
                details={
                    "kind": "io",
                    "input": str(call.arguments.get("url", "")),
                    "error": str(exc),
                },
            )

    return [
        AgentTool(
            definition=Tool(
                name="web_search",
                description=(
                    "Search the public web for current information. Returns "
                    "ranked titles, URLs, snippets, and model-readable "
                    "summaries. Use this when the answer depends on recent or "
                    "external information. Call web_fetch on a result URL "
                    "when its summary is not enough."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                        },
                        "count": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                        "need_content": {
                            "type": "boolean",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            handler=web_search,
        ),
        AgentTool(
            definition=Tool(
                name="web_fetch",
                description=(
                    "Fetch one public HTTP or HTTPS page and return its title "
                    "and content as Markdown. Use this after web_search or "
                    "when the user provides a public URL. It cannot access "
                    "local/private addresses, logins, or interactive pages; "
                    "use browser_bridge for those."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 8192,
                        }
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            handler=web_fetch,
        ),
    ]


__all__ = ["create_web_tools"]

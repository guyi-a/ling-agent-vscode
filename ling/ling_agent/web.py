"""Read-only DeepSeek Search and Fetch API client."""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_WEB_BASE_URL = "https://api.deepseek.com"
DEFAULT_WEB_TIMEOUT_SECONDS = 60.0
MAX_FETCH_URL_CHARS = 8_192
MAX_SEARCH_QUERY_CHARS = 128
MAX_RESULT_CONTENT_CHARS = 200_000


class WebClientError(RuntimeError):
    """A stable web API failure suitable for returning to the model."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


@dataclass(slots=True, frozen=True)
class WebClient:
    api_key: str
    base_url: str = DEFAULT_WEB_BASE_URL
    timeout_seconds: float = DEFAULT_WEB_TIMEOUT_SECONDS
    max_content_chars: int = MAX_RESULT_CONTENT_CHARS
    transport: httpx.AsyncBaseTransport | None = None

    async def search(
        self,
        query: str,
        *,
        count: int = 5,
        need_content: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > MAX_SEARCH_QUERY_CHARS:
            raise WebClientError(
                "INVALID_ARGUMENT",
                "query must contain 1 to 128 characters",
            )
        if not 1 <= count <= 50:
            raise WebClientError(
                "INVALID_ARGUMENT",
                "count must be between 1 and 50",
            )

        payload = await self._post(
            "search",
            {
                "query": query,
                "count": count,
                "need_content": need_content,
            },
            cancel_event,
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise WebClientError(
                "INVALID_RESPONSE",
                "Search API returned no results array",
            )

        results: list[dict[str, Any]] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            title = raw.get("title")
            url = raw.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            result: dict[str, Any] = {
                "title": title,
                "url": url,
                "snippet": _string(raw.get("snippet")),
                "summary": _string(raw.get("summary")),
                "siteName": _optional_string(raw.get("site_name")),
                "date": _optional_string(raw.get("date")),
                "authority": _optional_int(raw.get("authority")),
                "rerankScore": _optional_number(
                    raw.get("rerank_score")
                ),
            }
            if need_content:
                result["content"] = _truncate(
                    _string(raw.get("content")),
                    self.max_content_chars,
                )
            results.append(result)
        return {"results": results}

    async def fetch(
        self,
        url: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        validate_fetch_url(url)
        payload = await self._post(
            "fetch",
            {"url": url},
            cancel_event,
        )
        title = payload.get("title")
        content = payload.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            raise WebClientError(
                "INVALID_RESPONSE",
                "Fetch API returned an invalid response",
            )
        truncated = len(content) > self.max_content_chars
        return {
            "title": title,
            "content": _truncate(content, self.max_content_chars),
            "truncated": truncated,
        }

    async def _post(
        self,
        method: str,
        payload: dict[str, Any],
        cancel_event: asyncio.Event | None,
    ) -> dict[str, Any]:
        endpoint = f"{_web_root(self.base_url)}/v1/tools/{method}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self.transport,
        ) as client:
            request_task = asyncio.create_task(
                client.post(endpoint, json=payload, headers=headers)
            )
            cancel_task = (
                asyncio.create_task(cancel_event.wait())
                if cancel_event is not None
                else None
            )
            try:
                tasks = {request_task}
                if cancel_task is not None:
                    tasks.add(cancel_task)
                done, _ = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task is not None and cancel_task in done:
                    request_task.cancel()
                    await asyncio.gather(
                        request_task,
                        return_exceptions=True,
                    )
                    raise WebClientError(
                        "CANCELLED",
                        "The web request was cancelled",
                    )
                response = await request_task
            except asyncio.CancelledError:
                request_task.cancel()
                await asyncio.gather(
                    request_task,
                    return_exceptions=True,
                )
                raise
            except httpx.TimeoutException as exc:
                raise WebClientError(
                    "TIMEOUT",
                    "The web request timed out",
                    retryable=True,
                ) from exc
            except httpx.RequestError as exc:
                raise WebClientError(
                    "NETWORK_ERROR",
                    f"Unable to reach the web API: {exc}",
                    retryable=True,
                ) from exc
            finally:
                if cancel_task is not None:
                    cancel_task.cancel()
                    await asyncio.gather(
                        cancel_task,
                        return_exceptions=True,
                    )

        if response.status_code != 200:
            raise _http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise WebClientError(
                "INVALID_RESPONSE",
                "The web API returned invalid JSON",
            ) from exc
        if not isinstance(data, dict):
            raise WebClientError(
                "INVALID_RESPONSE",
                "The web API returned an invalid response",
            )
        return data


def validate_fetch_url(url: str) -> None:
    if not url or len(url) > MAX_FETCH_URL_CHARS:
        raise WebClientError(
            "INVALID_URL",
            "URL must contain 1 to 8192 characters",
        )
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise WebClientError("INVALID_URL", "URL is malformed") from exc
    if parsed.scheme not in ("http", "https") or not host:
        raise WebClientError(
            "INVALID_URL",
            "URL must use http or https and include a host",
        )
    if parsed.username is not None or parsed.password is not None:
        raise WebClientError(
            "INVALID_URL",
            "URL must not contain a username or password",
        )
    if is_private_web_target(url):
        raise WebClientError(
            "PRIVATE_URL",
            "Private, loopback, and local network URLs are blocked",
        )


def is_private_web_target(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return True
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _web_root(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value.endswith("/v1"):
        value = value[:-3].rstrip("/")
    return value or DEFAULT_WEB_BASE_URL


def _http_error(response: httpx.Response) -> WebClientError:
    status = response.status_code
    message = ""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if (
            isinstance(error, dict)
            and isinstance(error.get("message"), str)
        ):
            message = error["message"]
    if not message:
        message = response.text.strip()[:500] or f"HTTP {status}"
    code_by_status = {
        400: "INVALID_REQUEST",
        401: "AUTH_ERROR",
        402: "INSUFFICIENT_BALANCE",
        403: "POLICY_BLOCKED",
        404: "PAGE_UNAVAILABLE",
        408: "TIMEOUT",
        413: "REQUEST_TOO_LARGE",
        429: "RATE_LIMITED",
    }
    return WebClientError(
        code_by_status.get(status, "UPSTREAM_ERROR"),
        message,
        status=status,
        retryable=status in (408, 429) or status >= 500,
    )


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool)
        else None
    )


def _optional_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n\n[content truncated]"


__all__ = [
    "DEFAULT_WEB_BASE_URL",
    "DEFAULT_WEB_TIMEOUT_SECONDS",
    "MAX_FETCH_URL_CHARS",
    "MAX_SEARCH_QUERY_CHARS",
    "WebClient",
    "WebClientError",
    "is_private_web_target",
    "validate_fetch_url",
]

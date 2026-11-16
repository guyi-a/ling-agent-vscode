"""Shared HTTP, retry, and cancellation helpers for provider adapters."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import httpx

from ..stream import StreamOptions
from ..types import AssistantMessage

_T = TypeVar("_T")


class _Aborted(Exception):
    pass


class _ProviderHTTPError(Exception):
    def __init__(self, status: int, body: str, headers: httpx.Headers) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.headers = headers


@dataclass(slots=True)
class _AttemptState:
    started: bool = False
    partial: AssistantMessage | None = None


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, _ProviderHTTPError):
        should_retry = error.headers.get("x-should-retry")
        if should_retry == "true":
            return True
        if should_retry == "false":
            return False
        return error.status in (408, 409, 429) or error.status >= 500
    return isinstance(error, httpx.TransportError)


def _retry_delay(
    retry_index: int,
    options: StreamOptions,
    error: Exception,
) -> float:
    server_delay: float | None = None
    if isinstance(error, _ProviderHTTPError):
        retry_after_ms = error.headers.get("retry-after-ms")
        if retry_after_ms:
            try:
                server_delay = max(0.0, float(retry_after_ms) / 1000)
            except ValueError:
                pass
        retry_after = (
            error.headers.get("retry-after") if server_delay is None else None
        )
        if retry_after is not None:
            try:
                server_delay = max(0.0, float(retry_after))
            except ValueError:
                pass

    if server_delay is None:
        base_delay = max(0.0, options.retry_base_delay)
        delay = base_delay * 2**retry_index
    else:
        delay = server_delay
    return min(delay, max(0.0, options.retry_max_delay))


async def _await_with_cancel(
    factory: Callable[[], Awaitable[_T]],
    cancel_event: asyncio.Event | None,
) -> _T:
    if cancel_event is not None and cancel_event.is_set():
        raise _Aborted
    if cancel_event is None:
        return await factory()

    task = asyncio.create_task(factory())
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait(
        (task, cancel_task),
        return_when=asyncio.FIRST_COMPLETED,
    )
    if cancel_task in done:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        raise _Aborted

    cancel_task.cancel()
    with suppress(asyncio.CancelledError):
        await cancel_task
    return await task


__all__ = [
    "_Aborted",
    "_AttemptState",
    "_ProviderHTTPError",
    "_await_with_cancel",
    "_is_retryable_error",
    "_retry_delay",
]

"""Async event stream and the provider stream contract."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, TypeAlias

from .events import AssistantMessageEvent
from .types import AssistantMessage, Context, Model


@dataclass(slots=True)
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str | None = None
    # Set this event to request cancellation.
    cancel_event: asyncio.Event | None = None
    max_retries: int = 2
    retry_base_delay: float = 0.5
    retry_max_delay: float = 8.0


class AssistantMessageEventStream:
    """Async iterable of events with a final assistant message result."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AssistantMessageEvent | None] = asyncio.Queue()
        self._finished = asyncio.Event()
        self._final: AssistantMessage | None = None
        self._failure: BaseException | None = None
        self._iteration_ended = False
        self._producer_task: asyncio.Task[None] | None = None

    def attach_task(self, task: asyncio.Task[None]) -> None:
        """Keep a strong reference so the producer task is not garbage-collected."""
        self._producer_task = task

    def push(self, event: AssistantMessageEvent) -> None:
        if self._finished.is_set():
            return
        if event.type == "done":
            self._final = event.message
            self._finished.set()
        elif event.type == "error":
            self._final = event.error
            self._finished.set()
        self._queue.put_nowait(event)

    def end(self) -> None:
        if not self._finished.is_set():
            self._failure = RuntimeError("Stream ended without a terminal event")
            self._finished.set()
        self._queue.put_nowait(None)

    async def result(self) -> AssistantMessage:
        await self._finished.wait()
        if self._failure is not None:
            raise self._failure
        assert self._final is not None
        return self._final

    def __aiter__(self) -> "AssistantMessageEventStream":
        return self

    async def __anext__(self) -> AssistantMessageEvent:
        if self._iteration_ended:
            raise StopAsyncIteration
        event = await self._queue.get()
        if event is None:
            self._iteration_ended = True
            raise StopAsyncIteration
        if event.type in ("done", "error"):
            self._iteration_ended = True
        return event


StreamFunction: TypeAlias = Callable[
    [Model, Context, StreamOptions],
    AssistantMessageEventStream,
]


__all__ = [
    "AssistantMessageEventStream",
    "StreamFunction",
    "StreamOptions",
]

"""Bounded ephemeral stream frames and callback-based UI delivery."""

from __future__ import annotations

import inspect
from collections import deque
from typing import Awaitable, Callable

from .events import (
    AssistantTextDelta,
    AssistantThinkingDelta,
    CommittedEventEnvelope,
    EventSink,
    StreamFrame,
    StreamFrameEnvelope,
    ToolArgumentsDelta,
    ToolStatusChanged,
)

CommittedCallback = Callable[
    [CommittedEventEnvelope],
    None | Awaitable[None],
]
StreamCallback = Callable[
    [StreamFrameEnvelope],
    None | Awaitable[None],
]
ErrorCallback = Callable[[BaseException], None | Awaitable[None]]


class StreamFrameBuffer:
    """Keep a bounded window of live frames for each run."""

    def __init__(self, *, max_frames_per_run: int = 512) -> None:
        if max_frames_per_run < 1:
            raise ValueError("max_frames_per_run must be at least 1")
        self._max_frames_per_run = max_frames_per_run
        self._frames: dict[str, deque[StreamFrameEnvelope]] = {}
        self._next_seq: dict[str, int] = {}
        self._session_by_run: dict[str, str] = {}

    def append(
        self,
        *,
        session_id: str,
        run_id: str,
        turn_id: str,
        step: int,
        frame: StreamFrame,
        message_id: str | None = None,
        call_id: str | None = None,
    ) -> StreamFrameEnvelope:
        if step < 0:
            raise ValueError("step must not be negative")
        if isinstance(frame, (AssistantTextDelta, AssistantThinkingDelta)):
            if not message_id:
                raise ValueError("assistant stream frames require message_id")
        if isinstance(frame, (ToolArgumentsDelta, ToolStatusChanged)):
            if not call_id:
                raise ValueError("tool stream frames require call_id")

        owner = self._session_by_run.setdefault(run_id, session_id)
        if owner != session_id:
            raise ValueError("run_id belongs to a different session")

        frame_seq = self._next_seq.get(run_id, 1)
        self._next_seq[run_id] = frame_seq + 1
        envelope = StreamFrameEnvelope(
            session_id=session_id,
            run_id=run_id,
            turn_id=turn_id,
            step=step,
            frame_seq=frame_seq,
            frame=frame,
            message_id=message_id,
            call_id=call_id,
        )

        frames = self._frames.get(run_id)
        if frames is None:
            frames = deque(maxlen=self._max_frames_per_run)
            self._frames[run_id] = frames
        frames.append(envelope)
        return envelope

    def snapshot(
        self,
        run_id: str,
        *,
        after_frame_seq: int = 0,
    ) -> list[StreamFrameEnvelope]:
        if after_frame_seq < 0:
            raise ValueError("after_frame_seq must not be negative")
        return [
            frame
            for frame in self._frames.get(run_id, ())
            if frame.frame_seq > after_frame_seq
        ]

    def clear_run(self, run_id: str) -> None:
        self._frames.pop(run_id, None)
        self._next_seq.pop(run_id, None)
        self._session_by_run.pop(run_id, None)

    def clear(self) -> None:
        self._frames.clear()
        self._next_seq.clear()
        self._session_by_run.clear()


class CallbackEventSink(EventSink):
    """Deliver committed events and stream frames through UI callbacks."""

    def __init__(
        self,
        *,
        on_committed: CommittedCallback | None = None,
        on_stream_frame: StreamCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self._on_committed = on_committed
        self._on_stream_frame = on_stream_frame
        self._on_error = on_error

    async def emit_committed(self, envelope: CommittedEventEnvelope) -> None:
        if self._on_committed is not None:
            await self._invoke(self._on_committed, envelope)

    async def emit_stream_frame(self, envelope: StreamFrameEnvelope) -> None:
        if self._on_stream_frame is not None:
            await self._invoke(self._on_stream_frame, envelope)

    async def _invoke(self, callback: Callable[..., object], value: object) -> None:
        try:
            result = callback(value)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            if self._on_error is not None:
                try:
                    result = self._on_error(exc)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass


__all__ = [
    "CallbackEventSink",
    "CommittedCallback",
    "ErrorCallback",
    "StreamCallback",
    "StreamFrameBuffer",
]

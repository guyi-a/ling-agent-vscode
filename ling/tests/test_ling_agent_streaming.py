"""Tests for the live stream buffer and callback event sink."""

from __future__ import annotations

import asyncio

from ling_agent import (
    AssistantTextDelta,
    AssistantThinkingDelta,
    CallbackEventSink,
    CommittedEventEnvelope,
    RunStarted,
    StreamFrameBuffer,
    ToolArgumentsDelta,
    ToolStatusChanged,
)


def test_stream_frame_buffer_sequences_and_bounds() -> None:
    buffer = StreamFrameBuffer(max_frames_per_run=2)

    first = buffer.append(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        message_id="message-1",
        frame=AssistantTextDelta(delta="a"),
    )
    second = buffer.append(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        message_id="message-1",
        frame=AssistantThinkingDelta(delta="b"),
    )
    third = buffer.append(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        call_id="call-1",
        frame=ToolArgumentsDelta(delta="{}"),
    )

    assert [first.frame_seq, second.frame_seq, third.frame_seq] == [1, 2, 3]
    assert [frame.frame_seq for frame in buffer.snapshot("run-1")] == [2, 3]
    assert [frame.frame_seq for frame in buffer.snapshot("run-1", after_frame_seq=2)] == [
        3
    ]

    other = buffer.append(
        session_id="session-1",
        run_id="run-2",
        turn_id="turn-2",
        step=1,
        call_id="call-2",
        frame=ToolStatusChanged(status="running"),
    )
    assert other.frame_seq == 1
    assert buffer.snapshot("run-2") == [other]

    buffer.clear_run("run-1")
    assert buffer.snapshot("run-1") == []


def test_stream_frame_buffer_validation() -> None:
    buffer = StreamFrameBuffer()
    try:
        buffer.append(
            session_id="session-1",
            run_id="run-1",
            turn_id="turn-1",
            step=1,
            frame=AssistantTextDelta(delta="missing id"),
        )
    except ValueError as exc:
        assert "message_id" in str(exc)
    else:
        raise AssertionError("Expected message_id validation")

    buffer.append(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        message_id="message-1",
        frame=AssistantTextDelta(delta="ok"),
    )
    try:
        buffer.append(
            session_id="session-2",
            run_id="run-1",
            turn_id="turn-1",
            step=1,
            message_id="message-1",
            frame=AssistantTextDelta(delta="bad"),
        )
    except ValueError as exc:
        assert "different session" in str(exc)
    else:
        raise AssertionError("Expected run/session ownership validation")


async def _event_sink() -> None:
    committed: list[CommittedEventEnvelope] = []
    frames: list[object] = []
    errors: list[BaseException] = []

    async def on_committed(envelope: CommittedEventEnvelope) -> None:
        await asyncio.sleep(0)
        committed.append(envelope)

    def on_frame(envelope: object) -> None:
        frames.append(envelope)

    async def on_error(exc: BaseException) -> None:
        await asyncio.sleep(0)
        errors.append(exc)

    sink = CallbackEventSink(
        on_committed=on_committed,
        on_stream_frame=on_frame,
        on_error=on_error,
    )
    envelope = CommittedEventEnvelope(
        session_id="session-1",
        seq=1,
        event_id="event-1",
        run_id="run-1",
        turn_id="turn-1",
        step=0,
        event=RunStarted(),
    )
    await sink.emit_committed(envelope)
    buffer = StreamFrameBuffer()
    frame = buffer.append(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        message_id="message-1",
        frame=AssistantTextDelta(delta="hello"),
    )
    await sink.emit_stream_frame(frame)

    assert committed == [envelope]
    assert frames == [frame]
    assert errors == []


def test_callback_event_sink() -> None:
    asyncio.run(_event_sink())


async def _sink_failure_is_isolated() -> None:
    errors: list[BaseException] = []

    def fail(envelope: object) -> None:
        raise RuntimeError("frontend disconnected")

    async def on_error(exc: BaseException) -> None:
        errors.append(exc)

    sink = CallbackEventSink(on_stream_frame=fail, on_error=on_error)
    buffer = StreamFrameBuffer()
    frame = buffer.append(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        step=1,
        message_id="message-1",
        frame=AssistantTextDelta(delta="hello"),
    )

    await sink.emit_stream_frame(frame)
    assert len(errors) == 1
    assert "frontend disconnected" in str(errors[0])


def test_sink_failure_is_isolated() -> None:
    asyncio.run(_sink_failure_is_isolated())


if __name__ == "__main__":
    test_stream_frame_buffer_sequences_and_bounds()
    test_stream_frame_buffer_validation()
    test_callback_event_sink()
    test_sink_failure_is_isolated()
    print("all ling_agent streaming tests passed")

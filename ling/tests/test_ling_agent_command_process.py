"""Tests for managed command processes and foreground-to-background handoff."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from ling_agent.command_process import (
    CommandOutput,
    _read_stream,
    start_command_process,
)


def _sleep(seconds: float) -> str:
    if os.name == "nt":
        return f"Start-Sleep -Milliseconds {int(seconds * 1000)}"
    return f"sleep {seconds}"


def _echo(text: str) -> str:
    if os.name == "nt":
        return f"Write-Output {text}"
    return f"printf {text}"


async def _foreground_log_lifecycle(root: Path) -> None:
    log_path = root / "foreground.log"
    handle = await start_command_process(
        f"{_sleep(0.05)}; {_echo('fg-ok')}",
        cwd=str(root),
        max_output_chars=2_000,
        log_path=str(log_path),
    )
    outcome = await handle.wait_foreground(timeout_seconds=10)
    assert outcome.reason == "exit"
    assert outcome.exit_code == 0
    assert "fg-ok" in handle.tail()
    assert log_path.exists()
    handle.close_log(delete=True)
    assert not log_path.exists()


def test_foreground_completion_deletes_log() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_foreground_log_lifecycle(Path(temp)))


async def _detach_keeps_process(root: Path) -> None:
    log_path = root / "background.log"
    handle = await start_command_process(
        f"{_sleep(0.5)}; {_echo('bg-ok')}",
        cwd=str(root),
        max_output_chars=2_000,
        log_path=str(log_path),
    )
    detach = asyncio.Event()
    waiter = asyncio.create_task(
        handle.wait_foreground(
            timeout_seconds=10,
            detach_event=detach,
        )
    )
    await asyncio.sleep(0.1)
    detach.set()
    outcome = await waiter
    assert outcome.reason == "detached"
    assert handle.process.returncode is None
    exit_code = await handle.wait_background(drain_timeout=5)
    assert exit_code == 0
    assert "bg-ok" in handle.tail()
    assert "bg-ok" in log_path.read_text(encoding="utf-8")
    handle.close_log()


def test_detach_keeps_process_alive() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_detach_keeps_process(Path(temp)))


async def _cancel_kills_process(root: Path) -> None:
    handle = await start_command_process(
        _sleep(30),
        cwd=str(root),
        max_output_chars=1_000,
    )
    cancel = asyncio.Event()
    waiter = asyncio.create_task(
        handle.wait_foreground(
            timeout_seconds=60,
            cancel_event=cancel,
        )
    )
    await asyncio.sleep(0.1)
    cancel.set()
    outcome = await waiter
    assert outcome.reason == "cancelled"
    assert handle.process.returncode is not None


def test_foreground_cancel_kills_process() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_cancel_kills_process(Path(temp)))


async def _timeout_kills_process(root: Path) -> None:
    handle = await start_command_process(
        _sleep(30),
        cwd=str(root),
        max_output_chars=1_000,
    )
    outcome = await handle.wait_foreground(timeout_seconds=0.2)
    assert outcome.reason == "timeout"
    assert handle.process.returncode is not None


def test_foreground_timeout_kills_process() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_timeout_kills_process(Path(temp)))


async def _log_truncation(root: Path) -> None:
    log_path = root / "small.log"
    handle = await start_command_process(
        _echo("abcdefghij"),
        cwd=str(root),
        max_output_chars=2_000,
        log_path=str(log_path),
        max_log_chars=5,
    )
    outcome = await handle.wait_foreground(timeout_seconds=10)
    assert outcome.reason == "exit"
    assert outcome.exit_code == 0
    text = log_path.read_text(encoding="utf-8")
    assert "[log truncated]" in text
    handle.close_log()


def test_log_truncation_keeps_process_running() -> None:
    with tempfile.TemporaryDirectory() as temp:
        asyncio.run(_log_truncation(Path(temp)))


async def _unicode_across_chunks() -> None:
    reader = asyncio.StreamReader()
    output = CommandOutput(
        max_output_chars=1_000,
        tail_chars=1_000,
        max_log_chars=1_000,
    )

    async def feed() -> None:
        reader.feed_data("你".encode("utf-8")[:2])
        await asyncio.sleep(0)
        reader.feed_data("你".encode("utf-8")[2:] + "好".encode("utf-8"))
        reader.feed_eof()

    task = asyncio.create_task(_read_stream(reader, output, "stdout"))
    await feed()
    await task
    assert output.tail() == "你好"


def test_unicode_split_across_chunks() -> None:
    asyncio.run(_unicode_across_chunks())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

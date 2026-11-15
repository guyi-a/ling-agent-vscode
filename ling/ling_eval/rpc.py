"""Minimal NDJSON JSON-RPC client for a headless Ling sidecar."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence


class RpcError(RuntimeError):
    """An error response returned by the sidecar."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class RpcTimeout(RuntimeError):
    """A request did not receive a response before its timeout."""


class SidecarClient:
    """Talk to a sidecar process over stdin/stdout NDJSON."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        event_log: Path | None = None,
        max_line_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._command = list(command)
        self._env = dict(env) if env is not None else None
        self._cwd = str(cwd) if cwd is not None else None
        self._event_log = event_log
        self._max_line_bytes = max_line_bytes
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._event_file = None
        self.events: deque[dict[str, Any]] = deque(maxlen=100)
        self.failure_message: str | None = None
        self.cancelled = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    async def start(self) -> None:
        if self._proc is not None:
            return
        if self._event_log is not None:
            self._event_log.parent.mkdir(parents=True, exist_ok=True)
            self._event_file = self._event_log.open(
                "a",
                encoding="utf-8",
            )
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
            limit=self._max_line_bytes,
        )
        self._reader_task = asyncio.create_task(
            self._read_stdout(),
            name="ling-eval-stdout",
        )
        self._stderr_task = asyncio.create_task(
            self._read_stderr(),
            name="ling-eval-stderr",
        )

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Sidecar client is not started")
        if self._proc.returncode is not None:
            raise RpcError(
                -32000,
                f"Sidecar exited with code {self._proc.returncode}",
            )
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[Any] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._proc.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise RpcTimeout(
                f"Sidecar request timed out: {method}"
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def close(self, grace_seconds: float = 3.0) -> None:
        if self._proc is None:
            return
        if self._proc.returncode is None:
            try:
                await self.request("shutdown", {}, timeout=2.0)
            except (RpcError, RpcTimeout, RuntimeError, OSError):
                pass
            try:
                await asyncio.wait_for(
                    self._proc.wait(),
                    timeout=grace_seconds,
                )
            except asyncio.TimeoutError:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(
                        self._proc.wait(),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    RpcError(-32000, "Sidecar connection closed")
                )
        self._pending.clear()
        if self._event_file is not None:
            self._event_file.close()
            self._event_file = None

    async def _read_stdout(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                await self._handle_message(message)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        RpcError(-32000, "Sidecar connection closed")
                    )

    async def _handle_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is not None and request_id in self._pending:
            future = self._pending.pop(request_id)
            if future.done():
                return
            error = message.get("error")
            if isinstance(error, dict):
                future.set_exception(
                    RpcError(
                        int(error.get("code", -32000)),
                        str(error.get("message", "Sidecar error")),
                        error.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return
        if message.get("method") != "ui.event":
            return
        params = message.get("params")
        event = params.get("event") if isinstance(params, dict) else None
        if not isinstance(event, dict):
            return
        self.events.append(event)
        event_type = event.get("type")
        if event_type == "run.failed":
            self.failure_message = str(
                event.get("message") or "The agent run failed"
            )
        elif event_type == "run.cancelled":
            self.cancelled = True
        if self._event_file is not None:
            self._event_file.write(
                json.dumps(event, ensure_ascii=False) + "\n"
            )
            self._event_file.flush()

    async def _read_stderr(self) -> None:
        assert self._proc is not None
        assert self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            self._stderr_tail.append(
                line.decode("utf-8", errors="replace").rstrip()
            )


def default_sidecar_command() -> list[str]:
    """Return the development sidecar command."""

    root = Path(__file__).resolve().parents[1]
    entry = root / "ling_sidecar_entry.py"
    if entry.is_file():
        return [sys.executable, str(entry)]
    return [sys.executable, "-m", "ling_rpc.server"]


def sidecar_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a copy of the environment suitable for a sidecar process."""

    env = dict(base if base is not None else os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


__all__ = [
    "RpcError",
    "RpcTimeout",
    "SidecarClient",
    "default_sidecar_command",
    "sidecar_environment",
]

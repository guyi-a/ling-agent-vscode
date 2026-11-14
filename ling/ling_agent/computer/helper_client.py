"""Long-lived JSON-RPC client for the computer-use helper."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..paths import default_state_root
from .types import COMPUTER_PROTOCOL_VERSION, ComputerError


def _spawn_allowed(spawn_times: list[float], now: float) -> bool:
    """Allow at most two restarts (three spawns) per minute."""

    return sum(
        1 for spawned in spawn_times if now - spawned < 60.0
    ) < 3


class HelperClient:
    """Spawn and talk to one computer-use helper process."""

    def __init__(
        self,
        *,
        helper_path: str = "",
        init_timeout: float = 10.0,
        call_timeout: float = 30.0,
        on_notification: (
            Callable[[str, dict[str, Any]], None] | None
        ) = None,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        self._helper_path = helper_path.strip()
        self._init_timeout = init_timeout
        self._call_timeout = call_timeout
        self._on_notification = on_notification
        self._on_lost = on_lost
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._generation = 0
        self._closed = False
        self._spawn_times: list[float] = []
        self._log_path = default_state_root() / "computer" / "helper.log"

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise ComputerError(
                "HELPER_CLOSED",
                "The computer helper is closed.",
            )
        async with self._lock:
            await self._ensure_started_locked()
        return await self._request_locked(
            method,
            params or {},
            timeout=timeout or self._call_timeout,
        )

    async def close(self, timeout: float = 0.4) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "shutdown",
                    "params": {},
                }
                process.stdin.write(
                    (
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._kill_process(process)
        self._cancel_tasks()
        self._fail_pending(
            ComputerError(
                "HELPER_UNAVAILABLE",
                "The computer helper was shut down.",
            )
        )

    async def _ensure_started_locked(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            return
        self._cancel_tasks()
        self._process = None
        now = time.monotonic()
        if not _spawn_allowed(self._spawn_times, now):
            raise ComputerError(
                "HELPER_RESTART_LIMITED",
                "The computer helper restarted too often.",
                hint="Wait about a minute before retrying.",
            )
        recent = [
            spawned
            for spawned in self._spawn_times
            if now - spawned < 60.0
        ]
        self._spawn_times = recent + [now]
        command, cwd = self._command()
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self._environment(),
                limit=8 * 1024 * 1024,
                creationflags=flags,
            )
        except OSError as exc:
            raise ComputerError(
                "HELPER_UNAVAILABLE",
                f"Unable to start the computer helper: {exc}",
                hint="Check LING_COMPUTER_HELPER_PATH.",
            ) from exc
        self._process = process
        self._generation += 1
        generation = self._generation
        self._reader_task = asyncio.create_task(
            self._read_loop(process, generation),
            name="computer-helper-reader",
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(process),
            name="computer-helper-stderr",
        )
        try:
            await self._request_locked(
                "initialize",
                {
                    "protocolVersion": COMPUTER_PROTOCOL_VERSION,
                    "parentPid": os.getpid(),
                },
                timeout=self._init_timeout,
            )
        except Exception:
            await self._kill_process(process)
            raise

    async def _request_locked(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:
            raise ComputerError(
                "HELPER_UNAVAILABLE",
                "The computer helper is not running.",
            )
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            process.stdin.write(
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
            await process.stdin.drain()
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            await self._kill_process(process)
            raise ComputerError(
                "HELPER_TIMEOUT",
                f"The computer helper timed out on {method}.",
                hint="The helper was restarted. Observe before retrying.",
            ) from exc
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise ComputerError(
                "HELPER_UNAVAILABLE",
                f"The computer helper connection failed: {exc}",
            ) from exc
        error = response.get("error")
        if isinstance(error, dict):
            data = error.get("data")
            details = data if isinstance(data, dict) else {}
            code = details.get("code")
            if not code and error.get("code") == -32602:
                code = "INVALID_ARGS"
            raise ComputerError(
                str(code) if code else "HELPER_ERROR",
                str(error.get("message", "Computer helper error")),
                hint=str(details.get("hint", "")),
                may_have_taken_effect=bool(
                    details.get("mayHaveTakenEffect", False)
                ),
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def _read_loop(
        self,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        if process.stdout is None:
            return
        cancelled = False
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                request_id = payload.get("id")
                if not isinstance(request_id, int):
                    method = payload.get("method")
                    if (
                        isinstance(method, str)
                        and self._on_notification is not None
                    ):
                        params = payload.get("params")
                        try:
                            self._on_notification(
                                method,
                                params
                                if isinstance(params, dict)
                                else {},
                            )
                        except Exception:
                            pass
                    continue
                future = self._pending.pop(request_id, None)
                if future is not None and not future.done():
                    future.set_result(payload)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except (ConnectionResetError, OSError):
            pass
        finally:
            if self._generation == generation:
                self._process = None
            self._fail_pending(
                ComputerError(
                    "HELPER_UNAVAILABLE",
                    "The computer helper exited.",
                )
            )
            if (
                not cancelled
                and not self._closed
                and self._on_lost is not None
            ):
                try:
                    self._on_lost()
                except Exception:
                    pass

    async def _drain_stderr(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                self._append_log(line)
        except asyncio.CancelledError:
            raise
        except OSError:
            pass

    def _append_log(self, line: bytes) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            if (
                self._log_path.exists()
                and self._log_path.stat().st_size > 2_000_000
            ):
                backup = self._log_path.with_suffix(".log.1")
                self._log_path.replace(backup)
            with self._log_path.open("ab") as stream:
                stream.write(line)
        except OSError:
            pass

    async def _kill_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        self._cancel_tasks()
        self._fail_pending(
            ComputerError(
                "HELPER_UNAVAILABLE",
                "The computer helper was stopped.",
            )
        )

    def _cancel_tasks(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        self._reader_task = None
        self._stderr_task = None

    def _fail_pending(self, error: ComputerError) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _command(self) -> tuple[list[str], str | None]:
        parent_pid = str(os.getpid())
        if self._helper_path:
            return [
                self._helper_path,
                "--computer-helper",
                "--parent-pid",
                parent_pid,
            ], None
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--computer-helper",
                "--parent-pid",
                parent_pid,
            ], None
        root = str(Path(__file__).resolve().parents[2])
        return [
            sys.executable,
            "-m",
            "ling_agent.computer.helper",
            "--computer-helper",
            "--parent-pid",
            parent_pid,
        ], root

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in ("LING_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            env.pop(key, None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LING_COMPUTER_HELPER"] = "1"
        return env

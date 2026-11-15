"""Run the headless runner inside a Linux Docker container."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from .headless import HeadlessResult, LING_ROOT

CONTAINER_WORKSPACE = "/workspace"
CONTAINER_LING = "/ling"
CONTAINER_LOGS = "/logs"


def _mount(source: Path, target: str, *, read_only: bool = False) -> str:
    spec = (
        f"type=bind,source={source.resolve()},target={target}"
    )
    if read_only:
        spec += ",readonly"
    return spec


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def run_headless_in_docker(
    workspace: str | Path | None,
    prompt: str,
    *,
    image: str,
    logs_dir: str | Path,
    ling_root: str | Path = LING_ROOT,
    docker_bin: str = "docker",
    model: str | None = None,
    base_url: str | None = None,
    reasoning: str | None = None,
    timeout_seconds: float = 1800.0,
    poll_seconds: float = 0.5,
    max_steps: int | None = None,
    max_tokens: int | None = None,
    proxy: str = "",
    extra_env: Mapping[str, str] | None = None,
    container_workspace: str = CONTAINER_WORKSPACE,
    mount_workspace: bool = True,
    setup_command: str = "",
    python_bin: str = "python",
    path_prefix: str = "",
) -> HeadlessResult:
    """Run one prompt in a container and read its result JSON."""

    if mount_workspace and workspace is None:
        raise ValueError("workspace is required when mount_workspace is true")
    root = (
        Path(workspace).expanduser().resolve()
        if workspace is not None
        else None
    )
    logs = Path(logs_dir).expanduser().resolve()
    logs.mkdir(parents=True, exist_ok=True)
    prompt_path = logs / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    result_path = logs / "result.json"
    event_path = logs / "events.jsonl"
    result_path.unlink(missing_ok=True)
    name = f"ling-eval-{uuid.uuid4().hex[:12]}"
    args = [
        docker_bin,
        "run",
        "--rm",
        "--name",
        name,
        "--workdir",
        CONTAINER_LING,
    ]
    if mount_workspace and root is not None:
        args.extend(
            ["--mount", _mount(root, container_workspace)]
        )
    args.extend(
        [
            "--mount",
            _mount(Path(ling_root), CONTAINER_LING, read_only=True),
            "--mount",
            _mount(logs, CONTAINER_LOGS),
            "-e",
            "PYTHONPATH=/ling",
            "-e",
            "PYTHONUNBUFFERED=1",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "LING_COMPUTER_ENABLED=0",
            "-e",
            "HOME=/tmp",
        ]
    )
    for env_name in (
        "LING_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "LING_PROVIDER",
    ):
        if os.environ.get(env_name):
            args.extend(["-e", env_name])
    if model:
        args.extend(["-e", f"LING_MODEL={model}"])
    if base_url:
        args.extend(["-e", f"LING_API_BASE_URL={base_url}"])
    if proxy:
        args.extend(
            [
                "-e",
                f"HTTP_PROXY={proxy}",
                "-e",
                f"HTTPS_PROXY={proxy}",
                "-e",
                "NO_PROXY=localhost,127.0.0.1",
            ]
        )
    for key, value in (extra_env or {}).items():
        args.extend(["-e", f"{key}={value}"])

    inner = [
        python_bin,
        "-m",
        "ling_eval",
        "--workspace",
        container_workspace,
        "--prompt-file",
        f"{CONTAINER_LOGS}/prompt.txt",
        "--output",
        f"{CONTAINER_LOGS}/result.json",
        "--event-log",
        f"{CONTAINER_LOGS}/events.jsonl",
        "--timeout",
        str(timeout_seconds),
        "--poll",
        str(poll_seconds),
    ]
    if model:
        inner.extend(["--model", model])
    if reasoning:
        inner.extend(["--reasoning", reasoning])
    if max_steps is not None:
        inner.extend(["--max-steps", str(max_steps)])
    if max_tokens is not None:
        inner.extend(["--max-tokens", str(max_tokens)])
    prefix = (
        "git config --global --add safe.directory "
        f"{container_workspace} && "
        "git config --global core.autocrlf false"
    )
    if setup_command:
        prefix += " && " + setup_command
    if path_prefix:
        prefix += f" && export PATH={path_prefix}:$PATH"
    script = prefix + " && " + " ".join(inner)
    args.extend(
        [
            image,
            "bash",
            "-lc",
            script,
        ]
    )
    started_at = time.time()
    timed_out = False
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + 120.0,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        returncode = -1
        subprocess.run(
            [docker_bin, "kill", name],
            capture_output=True,
            text=True,
            check=False,
        )
    finished_at = time.time()
    if result_path.is_file():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            payload = {}
            stderr += f"\nInvalid result JSON: {exc}"
        result = HeadlessResult.from_dict(payload)
        result.workspace = (
            str(root) if root is not None else container_workspace
        )
        result.event_log = str(event_path)
        result.stderr_tail = _tail(
            "\n".join(
                part
                for part in (result.stderr_tail, stderr)
                if part
            )
        )
        if timed_out:
            result.status = "timeout"
            result.error = (
                result.error
                or f"Container exceeded {timeout_seconds:.0f}s"
            )
        return result
    error = (
        f"Container exited with code {returncode} without a result file"
    )
    if timed_out:
        error = f"Container exceeded {timeout_seconds:.0f}s"
    return HeadlessResult(
        status="timeout" if timed_out else "error",
        phase="unknown",
        session_id="",
        prompt=prompt,
        workspace=str(root) if root is not None else container_workspace,
        model=model or "",
        reasoning=reasoning or "",
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(finished_at - started_at, 3),
        base_revision="",
        patch="",
        error=error,
        event_log=str(event_path) if event_path.is_file() else "",
        stderr_tail=_tail(
            "\n".join(part for part in (stderr, stdout) if part)
        ),
    )


__all__ = ["run_headless_in_docker"]

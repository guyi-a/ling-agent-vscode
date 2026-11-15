"""SWE-bench instance adapter for the headless Ling runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .docker import run_headless_in_docker
from .headless import HeadlessResult, run_headless
from .rpc import default_sidecar_command

DEFAULT_LIMIT = 20
DEFAULT_MODEL_NAME = "ling-agent"
PROMPT_TEMPLATE = """You are working in a checked-out repository. Resolve the following issue by editing the repository.

Issue:
{problem_statement}

Work only inside the repository. Do not run git commands, do not commit, and do not modify test files. When finished, reply with a short summary of the changes."""


@dataclass(slots=True)
class SwebenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    repo_url: str = ""
    image: str = ""
    fail_to_pass: str = ""
    pass_to_pass: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SwebenchInstance":
        instance_id = row.get("instance_id")
        repo = row.get("repo")
        base_commit = row.get("base_commit")
        problem_statement = row.get("problem_statement")
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("instance_id must be a non-empty string")
        if not isinstance(repo, str) or not repo:
            raise ValueError(f"{instance_id}: repo must be a string")
        if not isinstance(base_commit, str) or not base_commit:
            raise ValueError(f"{instance_id}: base_commit must be a string")
        if not isinstance(problem_statement, str) or not problem_statement:
            raise ValueError(
                f"{instance_id}: problem_statement must be a string"
            )
        repo_url = row.get("repo_url", "")
        if not isinstance(repo_url, str):
            repo_url = ""
        image = row.get("image", "")
        if not isinstance(image, str):
            image = ""
        fail_to_pass = row.get("FAIL_TO_PASS", "")
        pass_to_pass = row.get("PASS_TO_PASS", "")
        return cls(
            instance_id=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=problem_statement,
            repo_url=repo_url,
            image=image,
            fail_to_pass=(
                fail_to_pass if isinstance(fail_to_pass, str) else ""
            ),
            pass_to_pass=(
                pass_to_pass if isinstance(pass_to_pass, str) else ""
            ),
        )


def load_instances(path: str | Path) -> list[SwebenchInstance]:
    """Load SWE-bench rows from JSONL or a JSON list."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Instance file not found: {source}")
    text = source.read_text(encoding="utf-8")
    rows: list[dict[str, Any]]
    if source.suffix.lower() == ".jsonl":
        rows = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("instances", payload.get("rows"))
        if not isinstance(payload, list):
            raise ValueError("JSON instance file must contain a list")
        rows = payload
    instances: list[SwebenchInstance] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each instance must be an object")
        instances.append(SwebenchInstance.from_row(row))
    return instances


def prompt_for(
    instance: SwebenchInstance,
    *,
    linux: bool = False,
) -> str:
    """Build the only text the agent sees for one instance."""

    prompt = PROMPT_TEMPLATE.format(
        problem_statement=instance.problem_statement.strip()
    )
    if linux:
        prompt += (
            "\n\nThe workspace runs in a Linux container. "
            "Use POSIX shell commands."
        )
    return prompt


def _safe_name(instance_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_id)


def _ensure_image(
    image: str,
    *,
    docker_bin: str,
    mirror: str,
) -> None:
    """Pull an instance image through a registry mirror when needed."""

    inspect = subprocess.run(
        [docker_bin, "image", "inspect", image],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if inspect.returncode == 0:
        return
    source = (
        f"{mirror.rstrip('/')}/{image}" if mirror else image
    )
    print(
        f"  pulling {source}",
        file=sys.stderr,
        flush=True,
    )
    pull = subprocess.run(
        [docker_bin, "pull", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=1800,
    )
    if pull.returncode != 0:
        raise RuntimeError(
            f"docker pull failed for {source}: "
            f"{pull.stderr.strip() or pull.stdout.strip()}"
        )
    if source != image:
        tag = subprocess.run(
            [docker_bin, "tag", source, image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if tag.returncode != 0:
            raise RuntimeError(
                f"docker tag failed for {source}: "
                f"{tag.stderr.strip() or tag.stdout.strip()}"
            )


def _git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    proxy: str = "",
    ssl_backend: str = "",
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-c", "core.autocrlf=false"]
    if ssl_backend:
        command.extend(["-c", f"http.sslBackend={ssl_backend}"])
    if proxy:
        command.extend(
            [
                "-c",
                f"http.proxy={proxy}",
                "-c",
                f"https.proxy={proxy}",
            ]
        )
    command.extend(args)
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git_ok(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    proxy: str = "",
    ssl_backend: str = "",
) -> str:
    result = _git(
        args,
        cwd=cwd,
        proxy=proxy,
        ssl_backend=ssl_backend,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _clone_url(instance: SwebenchInstance) -> str:
    if instance.repo_url:
        return instance.repo_url
    return f"https://github.com/{instance.repo}.git"


def prepare_repo(
    instance: SwebenchInstance,
    workspace: Path,
    *,
    repo_cache: Path | None = None,
    git_proxy: str = "",
    git_ssl_backend: str = "",
) -> Path:
    """Clone one instance's repository at its base commit."""

    workspace = workspace.resolve()
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    url = _clone_url(instance)
    if repo_cache is not None:
        repo_cache.mkdir(parents=True, exist_ok=True)
        cache = repo_cache / (
            instance.repo.replace("/", "__") + ".git"
        )
        if not cache.exists():
            _git_ok(
                ["clone", "--bare", url, str(cache)],
                proxy=git_proxy,
                ssl_backend=git_ssl_backend,
            )
        source = str(cache)
    else:
        source = url
    _git_ok(
        ["clone", source, str(workspace)],
        proxy=git_proxy,
        ssl_backend=git_ssl_backend,
    )
    check = _git(
        ["cat-file", "-e", f"{instance.base_commit}^{{commit}}"],
        cwd=workspace,
    )
    if check.returncode != 0:
        _git_ok(
            ["fetch", "origin", instance.base_commit],
            cwd=workspace,
            proxy=git_proxy,
            ssl_backend=git_ssl_backend,
        )
    _git_ok(
        ["checkout", "--force", instance.base_commit],
        cwd=workspace,
    )
    _git_ok(["reset", "--hard", instance.base_commit], cwd=workspace)
    _git_ok(["clean", "-fd"], cwd=workspace)
    head = _git_ok(["rev-parse", "HEAD"], cwd=workspace)
    if head != instance.base_commit:
        raise RuntimeError(
            f"{instance.instance_id}: checked out {head}, "
            f"expected {instance.base_commit}"
        )
    return workspace


def _prediction(
    instance: SwebenchInstance,
    result: HeadlessResult,
    model_name: str,
) -> dict[str, Any]:
    return {
        "instance_id": instance.instance_id,
        "model_patch": result.patch,
        "model_name_or_path": model_name,
    }


def _record(
    instance: SwebenchInstance,
    result: HeadlessResult,
) -> dict[str, Any]:
    payload = result.to_dict()
    payload.pop("patch", None)
    payload.pop("entries", None)
    payload["instanceId"] = instance.instance_id
    payload["repo"] = instance.repo
    payload["baseCommit"] = instance.base_commit
    return payload


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def run_instance(
    instance: SwebenchInstance,
    *,
    workdir: Path,
    predictions: Path,
    records: Path,
    repo_cache: Path | None,
    model_name: str,
    sidecar_command: Sequence[str] | None,
    timeout_seconds: float,
    poll_seconds: float,
    max_steps: int | None,
    max_tokens: int | None,
    model: str | None,
    base_url: str | None,
    reasoning: str | None,
    git_proxy: str,
    git_ssl_backend: str,
    keep_workspaces: bool,
    docker_image: str = "",
    docker_bin: str = "docker",
    docker_proxy: str = "",
    docker_mirror: str = "",
    official_images: bool = False,
) -> HeadlessResult:
    """Prepare one instance, run Ling, and append its prediction."""

    instance_dir = workdir / _safe_name(instance.instance_id)
    workspace = instance_dir / "workspace"
    logs = instance_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    if not official_images:
        prepare_repo(
            instance,
            workspace,
            repo_cache=repo_cache,
            git_proxy=git_proxy,
            git_ssl_backend=git_ssl_backend,
        )
    try:
        if official_images:
            if not instance.image:
                raise ValueError(
                    f"{instance.instance_id}: dataset has no image"
                )
            _ensure_image(
                instance.image,
                docker_bin=docker_bin,
                mirror=docker_mirror,
            )
            result = run_headless_in_docker(
                None,
                prompt_for(instance, linux=True),
                image=instance.image,
                logs_dir=logs,
                docker_bin=docker_bin,
                model=model,
                base_url=base_url,
                reasoning=reasoning,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                max_steps=max_steps,
                max_tokens=max_tokens,
                proxy=docker_proxy,
                container_workspace="/testbed",
                mount_workspace=False,
                setup_command=(
                    "/opt/miniconda3/bin/python -m pip install -q "
                    "-i https://pypi.tuna.tsinghua.edu.cn/simple "
                    "-r /ling/requirements.txt"
                ),
                python_bin="/opt/miniconda3/bin/python",
                path_prefix="/opt/miniconda3/envs/testbed/bin",
            )
        elif docker_image:
            result = run_headless_in_docker(
                workspace,
                prompt_for(instance, linux=True),
                image=docker_image,
                logs_dir=logs,
                docker_bin=docker_bin,
                model=model,
                base_url=base_url,
                reasoning=reasoning,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                max_steps=max_steps,
                max_tokens=max_tokens,
                proxy=docker_proxy,
            )
        else:
            result = await run_headless(
                workspace,
                prompt_for(instance),
                sidecar_command=sidecar_command,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                max_steps=max_steps,
                max_tokens=max_tokens,
                model=model,
                base_url=base_url,
                reasoning=reasoning,
                event_log=logs / "events.jsonl",
            )
        _append_jsonl(
            predictions,
            _prediction(instance, result, model_name),
        )
        records.mkdir(parents=True, exist_ok=True)
        (records / f"{_safe_name(instance.instance_id)}.json").write_text(
            json.dumps(
                _record(instance, result),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return result
    finally:
        if not keep_workspaces:
            shutil.rmtree(workspace, ignore_errors=True)


async def run_swebench(
    instances: Iterable[SwebenchInstance],
    *,
    workdir: str | Path,
    predictions: str | Path,
    records: str | Path,
    repo_cache: str | Path | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    sidecar_command: Sequence[str] | None = None,
    timeout_seconds: float = 1800.0,
    poll_seconds: float = 0.5,
    max_steps: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    base_url: str | None = None,
    reasoning: str | None = None,
    git_proxy: str = "",
    git_ssl_backend: str = "",
    keep_workspaces: bool = False,
    docker_image: str = "",
    docker_bin: str = "docker",
    docker_proxy: str = "",
    docker_mirror: str = "",
    official_images: bool = False,
) -> list[HeadlessResult]:
    """Run every instance sequentially and return their results."""

    work_root = Path(workdir).expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    predictions_path = Path(predictions).expanduser().resolve()
    records_path = Path(records).expanduser().resolve()
    cache_path = (
        Path(repo_cache).expanduser().resolve()
        if repo_cache
        else None
    )
    results: list[HeadlessResult] = []
    items = list(instances)
    for index, instance in enumerate(items, 1):
        print(
            f"[{index}/{len(items)}] {instance.instance_id} "
            f"({instance.repo})",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = await run_instance(
                instance,
                workdir=work_root,
                predictions=predictions_path,
                records=records_path,
                repo_cache=cache_path,
                model_name=model_name,
                sidecar_command=sidecar_command,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
                max_steps=max_steps,
                max_tokens=max_tokens,
                model=model,
                base_url=base_url,
                reasoning=reasoning,
                git_proxy=git_proxy,
                git_ssl_backend=git_ssl_backend,
                keep_workspaces=keep_workspaces,
                docker_image=docker_image,
                docker_bin=docker_bin,
                docker_proxy=docker_proxy,
                docker_mirror=docker_mirror,
                official_images=official_images,
            )
            results.append(result)
            print(
                f"  {result.status} in {result.duration_seconds:.1f}s, "
                f"patch {len(result.patch)} chars",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            records_path.mkdir(parents=True, exist_ok=True)
            error = {
                "instanceId": instance.instance_id,
                "repo": instance.repo,
                "baseCommit": instance.base_commit,
                "status": "error",
                "error": str(exc) or type(exc).__name__,
                "durationSeconds": 0,
            }
            (records_path / f"{_safe_name(instance.instance_id)}.json").write_text(
                json.dumps(error, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _append_jsonl(
                predictions_path,
                {
                    "instance_id": instance.instance_id,
                    "model_patch": "",
                    "model_name_or_path": model_name,
                },
            )
            print(
                f"  error: {error['error']}",
                file=sys.stderr,
                flush=True,
            )
    return results


def _existing_prediction_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        instance_id = payload.get("instance_id")
        if isinstance(instance_id, str):
            ids.add(instance_id)
    return ids


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ling_eval.swebench",
        description=(
            "Run SWE-bench instances through the headless Ling runner "
            "and write a predictions JSONL."
        ),
    )
    parser.add_argument("--instances", required=True)
    parser.add_argument(
        "--predictions",
        default="eval-data/predictions.jsonl",
    )
    parser.add_argument("--records", default="eval-data/records")
    parser.add_argument("--workdir", default="eval-data/runs")
    parser.add_argument("--repo-cache", default="eval-data/repos")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--reasoning",
        choices=("auto", "low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--sidecar")
    parser.add_argument("--git-proxy", default="")
    parser.add_argument("--git-ssl-backend", default="")
    parser.add_argument("--docker-image", default="")
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--docker-proxy", default="")
    parser.add_argument("--docker-mirror", default="")
    parser.add_argument("--official-images", action="store_true")
    parser.add_argument("--keep-workspaces", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    instances = load_instances(args.instances)
    if args.instance_id:
        wanted = set(args.instance_id)
        instances = [
            instance
            for instance in instances
            if instance.instance_id in wanted
        ]
    if args.limit > 0:
        instances = instances[: args.limit]
    if args.resume:
        existing = _existing_prediction_ids(Path(args.predictions))
        instances = [
            instance
            for instance in instances
            if instance.instance_id not in existing
        ]
    if not instances:
        print("no instances selected", file=sys.stderr)
        return 1
    command = (
        [args.sidecar]
        if args.sidecar
        else default_sidecar_command()
    )
    results = asyncio.run(
        run_swebench(
            instances,
            workdir=args.workdir,
            predictions=args.predictions,
            records=args.records,
            repo_cache=args.repo_cache,
            model_name=args.model_name,
            sidecar_command=command,
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
            max_steps=args.max_steps,
            max_tokens=args.max_tokens,
            model=args.model,
            base_url=args.base_url,
            reasoning=args.reasoning,
            git_proxy=args.git_proxy,
            git_ssl_backend=args.git_ssl_backend,
            keep_workspaces=args.keep_workspaces,
            docker_image=args.docker_image,
            docker_bin=args.docker_bin,
            docker_proxy=args.docker_proxy,
            docker_mirror=args.docker_mirror,
            official_images=args.official_images,
        )
    )
    completed = sum(1 for result in results if result.status == "completed")
    print(
        json.dumps(
            {
                "instances": len(instances),
                "completed": completed,
                "predictions": args.predictions,
                "records": args.records,
            },
            ensure_ascii=False,
        )
    )
    return 0 if completed == len(instances) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for the SWE-bench adapter."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ling_eval.swebench import (
    load_instances,
    prompt_for,
    run_swebench,
)

FAKE = Path(__file__).with_name("fake_eval_sidecar.py")


def _git_repo(root: Path) -> tuple[Path, str]:
    repo = root / "source"
    repo.mkdir()
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "add",
            "a.txt",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "base",
        ],
        cwd=repo,
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        encoding="utf-8",
    ).strip()
    return repo, commit


def test_load_instances_does_not_expose_hidden_fields() -> None:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "instances.jsonl"
        path.write_text(
            json.dumps(
                {
                    "instance_id": "local__1",
                    "repo": "local/repo",
                    "base_commit": "abc123",
                    "problem_statement": "fix the bug",
                    "patch": "GOLD PATCH",
                    "test_patch": "SECRET TEST",
                    "FAIL_TO_PASS": "[]",
                    "PASS_TO_PASS": "[]",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        instances = load_instances(path)
    assert len(instances) == 1
    instance = instances[0]
    assert instance.instance_id == "local__1"
    prompt = prompt_for(instance)
    assert "fix the bug" in prompt
    assert "SECRET TEST" not in prompt
    assert "GOLD PATCH" not in prompt


def test_swebench_adapter_with_local_repo() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source, commit = _git_repo(root)
        instances_path = root / "instances.jsonl"
        instances_path.write_text(
            json.dumps(
                {
                    "instance_id": "local__1",
                    "repo": "local/repo",
                    "repo_url": source.as_uri(),
                    "base_commit": commit,
                    "problem_statement": "fix the bug",
                    "patch": "GOLD",
                    "test_patch": "SECRET",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        predictions = root / "predictions.jsonl"
        records = root / "records"
        workdir = root / "runs"
        repo_cache = root / "repos"
        results = asyncio.run(
            run_swebench(
                load_instances(instances_path),
                workdir=workdir,
                predictions=predictions,
                records=records,
                repo_cache=repo_cache,
                sidecar_command=[
                    sys.executable,
                    "-u",
                    str(FAKE),
                    "--mode",
                    "complete",
                ],
                timeout_seconds=10.0,
                poll_seconds=0.05,
                keep_workspaces=True,
            )
        )
        assert len(results) == 1
        assert results[0].status == "completed"
        prediction = json.loads(
            predictions.read_text(encoding="utf-8").splitlines()[0]
        )
        assert prediction["instance_id"] == "local__1"
        assert "changed by agent" in prediction["model_patch"]
        assert "SECRET" not in prediction["model_patch"]
        record = json.loads(
            (records / "local__1.json").read_text(encoding="utf-8")
        )
        assert record["status"] == "completed"
        assert (repo_cache / "local__repo.git").is_dir()
        assert (
            workdir / "local__1" / "workspace" / "new.txt"
        ).is_file()


if __name__ == "__main__":
    test_load_instances_does_not_expose_hidden_fields()
    test_swebench_adapter_with_local_repo()
    print("swebench adapter tests passed")

"""Tests for the headless Ling runner."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from ling_eval.headless import export_patch, git_revision, run_headless

FAKE = Path(__file__).with_name("fake_eval_sidecar.py")


def _git_repo(root: Path) -> Path:
    repo = root / "repo"
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
    return repo


def _fake(mode: str) -> list[str]:
    return [sys.executable, "-u", str(FAKE), "--mode", mode]


def _run(
    repo: Path,
    mode: str,
    *,
    timeout: float = 10.0,
    log: Path | None = None,
    event_log: Path | None = None,
):
    extra = {"FAKE_SIDECAR_LOG": str(log)} if log else None
    return asyncio.run(
        run_headless(
            repo,
            "fix the issue",
            sidecar_command=_fake(mode),
            timeout_seconds=timeout,
            poll_seconds=0.05,
            isolate=True,
            event_log=event_log,
            extra_env=extra,
        )
    )


def test_headless_completes_and_exports_patch() -> None:
    with tempfile.TemporaryDirectory() as temp:
        repo = _git_repo(Path(temp))
        result = _run(
            repo,
            "complete",
            event_log=repo / "events.jsonl",
        )
        assert result.status == "completed"
        assert result.phase == "idle"
        assert result.session_id == "fake-session"
        assert result.base_revision
        assert result.assistant_text == "fake done"
        assert len(result.tool_calls) == 1
        assert "changed by agent" in result.patch
        assert "new.txt" in result.patch
        assert ".agents/spill.txt" not in result.patch
        assert "events.jsonl" not in result.patch
        assert result.error is None


def test_export_patch_uses_base_commit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        repo = _git_repo(Path(temp))
        base = git_revision(repo)
        (repo / "a.txt").write_text("agent change\n", encoding="utf-8")
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-am",
                "agent commit",
            ],
            cwd=repo,
            check=True,
        )
        patch = export_patch(repo, base=base)
        assert "agent change" in patch


def test_headless_resolves_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repo = _git_repo(root)
        log = root / "fake.log"
        result = _run(repo, "approval", log=log)
        assert result.status == "completed"
        assert "approval" in log.read_text(encoding="utf-8")


def test_headless_reports_failure() -> None:
    with tempfile.TemporaryDirectory() as temp:
        repo = _git_repo(Path(temp))
        result = _run(repo, "fail")
        assert result.status == "error"
        assert result.error == "fake failure"


def test_headless_timeout_cancels_run() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repo = _git_repo(root)
        log = root / "fake.log"
        result = _run(repo, "timeout", timeout=0.5, log=log)
        assert result.status == "timeout"
        assert "cancel" in log.read_text(encoding="utf-8")


if __name__ == "__main__":
    test_headless_completes_and_exports_patch()
    test_export_patch_uses_base_commit()
    test_headless_resolves_approval()
    test_headless_reports_failure()
    test_headless_timeout_cancels_run()
    print("headless eval tests passed")

"""Summarize a Ling evaluation batch and its official report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _records(records_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not records_dir.is_dir():
        return records
    for path in sorted(records_dir.glob("*.json")):
        payload = _read_json(path)
        instance_id = payload.get("instanceId")
        if isinstance(instance_id, str):
            records[instance_id] = payload
    return records


def _per_instance_reports(
    logs_dir: Path,
    run_id: str,
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    root = logs_dir / run_id
    if not root.is_dir():
        return reports
    for path in root.glob("*/*/report.json"):
        payload = _read_json(path)
        for instance_id, report in payload.items():
            if isinstance(report, dict):
                reports[instance_id] = report
    return reports


def _usage(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "modelCalls": 0,
    }
    for record in records.values():
        usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = usage.get(key, 0)
            if isinstance(value, int):
                totals[key] += value
    return totals


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ling_eval.summarize",
        description="Summarize a Ling SWE-bench batch.",
    )
    parser.add_argument("--records", required=True)
    parser.add_argument("--report", default="")
    parser.add_argument("--logs", default="logs/run_evaluation")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    records = _records(Path(args.records))
    report = _read_json(Path(args.report)) if args.report else {}
    logs_dir = Path(args.logs)
    if not logs_dir.is_dir():
        parent = Path("..") / logs_dir
        if parent.is_dir():
            logs_dir = parent
    per_instance = (
        _per_instance_reports(logs_dir, args.run_id)
        if args.run_id
        else {}
    )
    submitted = (
        report.get("submitted_instances")
        if isinstance(report.get("submitted_instances"), int)
        else len(records)
    )
    resolved = (
        report.get("resolved_instances")
        if isinstance(report.get("resolved_instances"), int)
        else 0
    )
    usage = _usage(records)
    rows = []
    for instance_id, record in sorted(records.items()):
        official = per_instance.get(instance_id, {})
        rows.append(
            {
                "instanceId": instance_id,
                "status": record.get("status"),
                "durationSeconds": record.get("durationSeconds"),
                "tokens": (record.get("usage") or {}).get("totalTokens", 0),
                "patchBytes": record.get("patchBytes"),
                "resolved": official.get("resolved"),
                "patchApplied": official.get(
                    "patch_successfully_applied"
                ),
                "error": record.get("error"),
            }
        )
    summary = {
        "submitted": submitted,
        "resolved": resolved,
        "score": (
            round(resolved / submitted, 4) if submitted else 0.0
        ),
        "scoreText": f"{resolved}/{submitted}",
        "usage": usage,
        "instances": rows,
        "report": args.report,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

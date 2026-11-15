"""Command-line entry point for one headless Ling run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .headless import run_headless
from .rpc import default_sidecar_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ling_eval",
        description=(
            "Run one prompt against a headless Ling sidecar and export "
            "the resulting git patch."
        ),
    )
    parser.add_argument("--workspace", required=True)
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--approval-mode", default="auto")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--reasoning",
        choices=("auto", "low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--command-timeout", type=float)
    parser.add_argument("--sidecar")
    parser.add_argument("--output")
    parser.add_argument("--event-log")
    parser.add_argument("--no-isolate", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = args.prompt
    if not prompt.strip():
        print("prompt is empty", file=sys.stderr)
        return 2
    if not any(
        os.environ.get(name)
        for name in ("LING_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    ):
        print(
            "warning: no LING_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY "
            "is set; the sidecar will fail unless a fake sidecar is used.",
            file=sys.stderr,
        )
    command = (
        [args.sidecar]
        if args.sidecar
        else default_sidecar_command()
    )
    result = asyncio.run(
        run_headless(
            args.workspace,
            prompt,
            sidecar_command=command,
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
            approval_mode=args.approval_mode,
            model=args.model,
            base_url=args.base_url,
            reasoning=args.reasoning,
            max_steps=args.max_steps,
            max_tokens=args.max_tokens,
            command_timeout_seconds=args.command_timeout,
            isolate=not args.no_isolate,
            event_log=args.event_log,
        )
    )
    payload = result.to_dict()
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "durationSeconds": result.duration_seconds,
                    "patchBytes": len(result.patch.encode("utf-8")),
                    "toolCalls": len(result.tool_calls),
                    "output": str(output),
                    "eventLog": result.event_log,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

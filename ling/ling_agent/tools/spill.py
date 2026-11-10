"""Spill oversized tool results into workspace files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet

from ling_ai.types import TextContent
from ling_core import PostToolUse, PostToolUseDecision

from ..config import HarnessConfig
from ..workspace import Workspace


@dataclass(slots=True, frozen=True)
class SpillResult:
    text: str
    path: str | None = None
    spilled: bool = False


def maybe_spill_output(
    workspace: Workspace,
    *,
    session_id: str,
    call_id: str,
    text: str,
    config: HarnessConfig,
) -> SpillResult:
    if len(text) <= config.spill_threshold_chars:
        return SpillResult(text=_truncate(text, config.max_tool_result_chars))

    relative_path = (
        Path(".ling")
        / "spills"
        / _safe_component(session_id)
        / f"{_safe_component(call_id)}.txt"
    )
    absolute_path = workspace.root / relative_path
    try:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(text, encoding="utf-8")
    except OSError:
        return SpillResult(text=_truncate(text, config.max_tool_result_chars))

    preview = text[: config.spill_preview_chars].rstrip()
    lines = text.count("\n") + 1
    byte_count = len(text.encode("utf-8"))
    notice = (
        f"[Output spilled to {relative_path.as_posix()}: "
        f"{lines} lines, {byte_count} bytes.]\n"
        "Use read_file on that path to inspect the full output."
    )
    combined = f"{preview}\n\n{notice}"
    return SpillResult(
        text=_truncate(combined, config.max_tool_result_chars),
        path=relative_path.as_posix(),
        spilled=True,
    )


def create_spill_hook(
    workspace: Workspace,
    *,
    session_id: str,
    config: HarnessConfig,
    exempt_tools: AbstractSet[str] = frozenset(),
):
    def spill_hook(event: PostToolUse) -> PostToolUseDecision | None:
        if event.call.name in exempt_tools:
            return None

        output = event.output
        if isinstance(output, str):
            result = maybe_spill_output(
                workspace,
                session_id=session_id,
                call_id=event.call.id,
                text=output,
                config=config,
            )
            if result.text != output:
                return PostToolUseDecision(output=result.text)
            return None

        if isinstance(output, list):
            text_blocks = [
                block for block in output if isinstance(block, TextContent)
            ]
            if len(text_blocks) != len(output):
                return None
            combined = "\n".join(block.text for block in text_blocks)
            result = maybe_spill_output(
                workspace,
                session_id=session_id,
                call_id=event.call.id,
                text=combined,
                config=config,
            )
            if result.text != combined:
                return PostToolUseDecision(
                    output=[TextContent(result.text)]
                )

        return None

    return spill_hook


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[truncated]"


def _safe_component(value: str) -> str:
    safe = "".join(
        character
        if character.isalnum() or character in "-_."
        else "_"
        for character in value
    )
    return safe[:120] or "item"


__all__ = ["SpillResult", "create_spill_hook", "maybe_spill_output"]

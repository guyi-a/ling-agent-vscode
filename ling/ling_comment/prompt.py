"""Prompt construction for comment generation."""

from __future__ import annotations

from .targets import CommentTarget, Parameter, TargetAnalysis

SYSTEM_PROMPT = """You write API documentation comments for source code.

Rules:
- Return only the comment text, with no explanation and no Markdown fences.
- For Python, return a docstring delimited by triple double quotes.
- Use Google style: a summary line, then Args and Returns sections when
  needed.
- Document every parameter except self and cls.
- Match the language of existing comments in the code. If there are none,
  write in Chinese.
- Only describe behavior visible in the code. Never invent parameters,
  return values, or exceptions.
- Do not indent the comment; it will be indented to match the code.
"""


def build_user_prompt(
    target: CommentTarget,
    analysis: TargetAnalysis,
) -> str:
    lines = [
        f"Language: {target.language_id}",
        f"Symbol: {target.symbol_name} ({target.symbol_kind})",
        f"Signature: {analysis.signature}",
    ]
    if analysis.parameters:
        lines.append("Parameters:")
        lines.extend(
            _format_parameter(parameter)
            for parameter in analysis.parameters
        )
    lines.append(f"Returns: {analysis.returns or 'None'}")
    if analysis.raises:
        lines.append(f"Raises: {', '.join(analysis.raises)}")
    if analysis.decorators:
        lines.append(f"Decorators: {', '.join(analysis.decorators)}")
    if analysis.class_context:
        lines.append(f"Class context: {analysis.class_context}")

    lines.extend(
        [
            "",
            "Source:",
            "<source>",
            target.source_text.strip(),
            "</source>",
            "",
            "Write the docstring.",
        ]
    )
    return "\n".join(lines)


def _format_parameter(parameter: Parameter) -> str:
    parts = [f"- {parameter.name}"]
    if parameter.annotation:
        parts.append(f": {parameter.annotation}")
    else:
        parts.append(": no annotation")
    if parameter.default is not None:
        parts.append(f" = {parameter.default}")
    return "".join(parts)


__all__ = ["SYSTEM_PROMPT", "build_user_prompt"]

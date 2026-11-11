"""Prompt construction for comment generation."""

from __future__ import annotations

from .targets import (
    PYTHON_LANGUAGES,
    CommentTarget,
    Parameter,
    TargetAnalysis,
)

BASE_SYSTEM_PROMPT = """You write API documentation comments for source code.

Rules:
- Return only the comment text, with no explanation and no Markdown fences.
- {format_rule}
- Document every parameter except self, cls, and this.
- Match the language of existing comments in the code. If there are none,
  write in Chinese.
- Only describe behavior visible in the code. Never invent parameters,
  return values, or exceptions.
- If an existing comment is provided, rewrite it: keep accurate parts,
  fix omissions, and remove anything the code does not support.
- Do not indent the comment; it will be indented to match the code.
"""

PYTHON_FORMAT_RULE = (
    "For Python, return a docstring delimited by triple double quotes. "
    "Use Google style: a summary line, then Args and Returns sections "
    "when needed."
)

JS_FORMAT_RULE = (
    "For TypeScript or JavaScript, return a JSDoc comment delimited by "
    "/** and */. Use @param {Type} name - description for parameters "
    "and @returns {Type} description for the return value when it is "
    "not void."
)


def system_prompt(language_id: str) -> str:
    return BASE_SYSTEM_PROMPT.format(
        format_rule=(
            PYTHON_FORMAT_RULE
            if language_id in PYTHON_LANGUAGES
            else JS_FORMAT_RULE
        )
    )


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
    if analysis.existing_comment:
        lines.extend(
            [
                "",
                "Existing comment:",
                "<existing>",
                analysis.existing_comment,
                "</existing>",
                "Rewrite it, keep accurate information, and fill gaps.",
            ]
        )

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


__all__ = ["build_user_prompt", "system_prompt"]

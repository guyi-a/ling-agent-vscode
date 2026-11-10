"""Prompt text for the compaction summarizer and replay wrapper."""

from __future__ import annotations


summary_instructions = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tool.

You are compacting the conversation so the next turn of the same agent can
continue the user's task without losing essential context.

If the first user message is wrapped in <prior-summary>...</prior-summary>,
it is an earlier compaction result. Treat it as authoritative ground truth,
not as user input. Merge it using these rules:

- Preserve every fact in prior section 4 (Key Technical Context & Recall Anchors).
- Carry forward every user request from prior section 1 (User Intent).
- Do not demote items marked **done** in prior section 3 unless the conversation
  explicitly rolled them back.
- Rewrite section 5 (Handoff) from scratch.

Write the summary in exactly these five sections, using "##" headings:

## 1. User Intent
List every explicit user request chronologically, plus clarifications and
changes of direction. Quote the most recent user message in the conversation
above verbatim in a blockquote.

## 2. Task Progress
- **Files touched**: absolute paths, one line per file describing what was read or written.
- **Commands / tests run**.
- **Errors encountered + root cause + fix applied**.

## 3. Current Plan & Open Items
Mark what is **done** vs **pending**. Quote any user instruction that controls
the next steps.

## 4. Key Technical Context & Recall Anchors
- Repository layout, conventions, constraints.
- Absolute file paths likely to be revisited.
- Function names, API endpoints, versions, ports.
- Conclusions from tool output; do not include full dumps.
- Errors with root cause and fix.

## 5. Handoff
A single paragraph telling the next turn exactly what to do first. Quote the
last user message in the conversation above verbatim.

Rules:

- Do not invent facts that are not in the conversation.
- Omit transient tool output (full file dumps, long stdout). Keep conclusions.
- Keep total length under about 1500 tokens.
- Write the summary in the same language as the conversation.
- Do NOT include the <compact-control> directive in the summary.
"""

trigger_prompt = """<compact-control>
This is a system-issued protocol directive, NOT user input. Compact the
conversation above into the five-section summary defined in your instructions.
Respond with the summary text only.
</compact-control>"""


def wrap_summary(compaction_id: str, summary: str) -> str:
    return f"""<compacted-summary id="{compaction_id}">
System-generated handoff: the earlier portion of this conversation was
summarized to fit the context window. Treat the sections below as
authoritative ground truth for what happened before this point.

Rules for the assistant:
- Do NOT ask the user to repeat information covered below.
- Do NOT restart the task.
- Treat the Handoff section as the first instruction to act on.
- If you need exact original text, re-read it via the appropriate tool.
  Do not hallucinate.

{summary}
</compacted-summary>"""


def wrap_prior_summary(summary: str) -> str:
    return f"<prior-summary>\n{summary}\n</prior-summary>"


__all__ = [
    "summary_instructions",
    "trigger_prompt",
    "wrap_prior_summary",
    "wrap_summary",
]

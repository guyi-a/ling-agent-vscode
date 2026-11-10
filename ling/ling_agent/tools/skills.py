"""Tool for loading task playbooks on demand."""

from __future__ import annotations

import json
from typing import Sequence

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext, ToolExecution

from ..skills import Skill


def create_skill_tools(
    skills: Sequence[Skill],
    *,
    max_body_chars: int = 50_000,
) -> list[AgentTool]:
    by_name = {skill.name: skill for skill in skills}

    def load_skill(call: ToolCall, context: ToolContext) -> str | ToolExecution:
        raw_name = call.arguments.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise TypeError("name must be a non-empty string")

        skill = by_name.get(raw_name.strip())
        if skill is None:
            payload = {
                "ok": False,
                "message": f'skill "{raw_name.strip()}" not found',
                "available": sorted(by_name),
            }
            return ToolExecution(
                output=json.dumps(payload, ensure_ascii=False),
                is_error=True,
            )

        body = skill.body
        truncated = len(body) > max_body_chars
        if truncated:
            body = body[:max_body_chars] + "\n\n[truncated]"

        scripts_path = skill.dir_path / "scripts"
        payload = {
            "ok": True,
            "name": skill.name,
            "body": body,
            "skill_path": str(skill.dir_path),
        }
        if truncated:
            payload["truncated"] = True
        if scripts_path.is_dir():
            payload["scripts_path"] = str(scripts_path)
        return json.dumps(payload, ensure_ascii=False)

    return [
        AgentTool(
            definition=Tool(
                name="load_skill",
                description=(
                    "Load a task playbook by name. Returns the SKILL.md body "
                    "plus the absolute skill_path and scripts_path."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ),
            handler=load_skill,
        )
    ]


__all__ = ["create_skill_tools"]

"""Tool for asking the user structured questions."""

from __future__ import annotations

import json

from ling_ai.types import Tool, ToolCall
from ling_core import AgentTool, ToolContext

from ..questions import (
    MAX_OPTIONS,
    MAX_QUESTIONS,
    QuestionHandler,
    QuestionRequest,
    answer_to_dict,
    normalize_questions,
)


def create_question_tools(
    handler: QuestionHandler | None,
) -> list[AgentTool]:
    if handler is None:
        return []

    async def ask_user(call: ToolCall, context: ToolContext) -> str:
        questions = normalize_questions(call.arguments.get("questions"))
        request = QuestionRequest(
            call_id=call.id,
            questions=tuple(questions),
        )
        answers = await handler.request(request, context.cancel_event)
        if answers is None:
            return json.dumps(
                {
                    "cancelled": True,
                    "message": (
                        "The user did not answer. Proceed with a sensible "
                        "default, choose an alternative, or stop this branch; "
                        "do not repeat the same question."
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "cancelled": False,
                "answers": [answer_to_dict(answer) for answer in answers],
            },
            ensure_ascii=False,
        )

    return [
        AgentTool(
            definition=Tool(
                name="ask_user",
                description=(
                    "Ask the user one or more multiple-choice questions and "
                    "wait for the answer. Use this when a decision cannot be "
                    "inferred from the workspace."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_QUESTIONS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "question": {"type": "string"},
                                    "header": {"type": "string"},
                                    "multi_select": {"type": "boolean"},
                                    "options": {
                                        "type": "array",
                                        "maxItems": MAX_OPTIONS,
                                        "items": {
                                            "anyOf": [
                                                {"type": "string"},
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "label": {
                                                            "type": "string"
                                                        },
                                                        "description": {
                                                            "type": "string"
                                                        },
                                                    },
                                                    "required": ["label"],
                                                    "additionalProperties": False,
                                                },
                                            ]
                                        },
                                    },
                                },
                                "required": ["question"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["questions"],
                    "additionalProperties": False,
                },
            ),
            handler=ask_user,
        )
    ]


__all__ = ["create_question_tools"]

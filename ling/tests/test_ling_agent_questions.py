"""Tests for ask_user normalization, waiting, and tool behavior."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from ling_agent import (
    QuestionAnswer,
    QuestionManager,
    QuestionRequest,
    Workspace,
    create_question_tools,
    derive_effect,
    normalize_answers,
    normalize_questions,
)
from ling_ai.types import ToolCall
from ling_core import ToolContext

CONTEXT = ToolContext(step=1)


class FakeHandler:
    def __init__(self, answers: list[QuestionAnswer] | None) -> None:
        self.answers = answers
        self.requests: list[QuestionRequest] = []
        self.cancelled = False

    async def request(
        self,
        request: QuestionRequest,
        cancel_event: asyncio.Event | None = None,
    ) -> list[QuestionAnswer] | None:
        self.requests.append(request)
        return self.answers

    async def cancel_all(self, reason: str = "") -> None:
        self.cancelled = True

    def pending(self) -> list[QuestionRequest]:
        return []


def test_normalize_questions_accepts_string_and_object_options() -> None:
    questions = normalize_questions(
        [
            {
                "question": "Pick one",
                "options": [
                    "Fast",
                    {"label": "Pretty", "description": "More layout work"},
                ],
            },
            {
                "id": "second",
                "question": "Anything else?",
                "header": "Extra",
                "multi_select": True,
            },
        ]
    )
    assert [question.id for question in questions] == ["q1", "second"]
    assert questions[0].options[0].label == "Fast"
    assert questions[0].options[1].description == "More layout work"
    assert questions[1].header == "Extra"
    assert questions[1].multi_select is True


def test_normalize_questions_rejects_invalid_payloads() -> None:
    with pytest.raises(ValueError, match="non-empty array"):
        normalize_questions([])
    with pytest.raises(ValueError, match="at most 4"):
        normalize_questions(
            [{"question": "x"} for _ in range(5)]
        )
    with pytest.raises(ValueError, match="duplicate question id"):
        normalize_questions(
            [
                {"id": "same", "question": "a"},
                {"id": "same", "question": "b"},
            ]
        )
    with pytest.raises(ValueError, match="duplicate option"):
        normalize_questions(
            [{"question": "x", "options": ["A", "A"]}]
        )


def test_normalize_answers_validates_selection() -> None:
    request = QuestionRequest(
        call_id="call-1",
        questions=tuple(
            normalize_questions(
                [
                    {
                        "id": "style",
                        "question": "Style?",
                        "options": ["Fast", "Pretty"],
                    }
                ]
            )
        ),
    )
    answers = normalize_answers(
        request,
        [{"id": "style", "selected": ["Pretty"]}],
    )
    assert answers[0].selected == ("Pretty",)

    with pytest.raises(ValueError, match="unknown options"):
        normalize_answers(
            request,
            [{"id": "style", "selected": ["Other"]}],
        )
    with pytest.raises(ValueError, match="only one selection"):
        normalize_answers(
            request,
            [{"id": "style", "selected": ["Fast", "Pretty"]}],
        )
    with pytest.raises(ValueError, match="unknown answer ids"):
        normalize_answers(request, [{"id": "missing"}])


def test_question_manager_resolves_and_cancels() -> None:
    async def scenario() -> None:
        manager = QuestionManager()
        questions = tuple(
            normalize_questions([{"question": "Pick", "options": ["A", "B"]}])
        )
        request = QuestionRequest("call-1", questions)
        future = manager.open(request)
        waiting = asyncio.create_task(
            manager.wait("call-1", future, None)
        )
        await asyncio.sleep(0)
        assert manager.pending() == [request]

        assert manager.resolve(
            "call-1",
            [QuestionAnswer(id="q1", selected=("A",))],
        )
        answers = await waiting
        assert answers is not None
        assert answers[0].selected == ("A",)
        assert manager.pending() == []

        future = manager.open(QuestionRequest("call-2", questions))
        cancelled = asyncio.Event()
        cancelled.set()
        assert await manager.wait("call-2", future, cancelled) is None
        assert manager.pending() == []

    asyncio.run(scenario())


def test_question_manager_cancel_all_resolves_pending() -> None:
    async def scenario() -> None:
        manager = QuestionManager()
        questions = tuple(normalize_questions([{"question": "Pick"}]))
        future = manager.open(QuestionRequest("call-1", questions))
        manager.cancel_all("cancelled")
        assert await future is None
        assert manager.pending() == []

    asyncio.run(scenario())


def test_ask_user_tool_returns_answers_json() -> None:
    handler = FakeHandler(
        [QuestionAnswer(id="q1", selected=("Pretty",))]
    )
    tool = create_question_tools(handler)[0]

    output = asyncio.run(
        tool.handler(
            ToolCall(
                id="call-1",
                name="ask_user",
                arguments={
                    "questions": [
                        {
                            "question": "Style?",
                            "options": ["Fast", "Pretty"],
                        }
                    ]
                },
            ),
            CONTEXT,
        )
    )
    payload = json.loads(output)
    assert payload["cancelled"] is False
    assert payload["answers"] == [{"id": "q1", "selected": ["Pretty"]}]
    assert handler.requests[0].call_id == "call-1"


def test_ask_user_tool_reports_cancellation() -> None:
    tool = create_question_tools(FakeHandler(None))[0]
    output = asyncio.run(
        tool.handler(
            ToolCall(
                id="call-1",
                name="ask_user",
                arguments={"questions": [{"question": "Pick"}]},
            ),
            CONTEXT,
        )
    )
    payload = json.loads(output)
    assert payload["cancelled"] is True
    assert "do not repeat" in payload["message"]


def test_ask_user_tool_is_absent_without_handler() -> None:
    assert create_question_tools(None) == []


def test_ask_user_has_no_approval_effect() -> None:
    with tempfile.TemporaryDirectory() as temp:
        effect = derive_effect(
            ToolCall(
                id="call-1",
                name="ask_user",
                arguments={"questions": [{"question": "Pick"}]},
            ),
            Workspace(Path(temp)),
        )
        assert effect.kind == "meta"
        assert effect.operation == "ask_user"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))

"""Interactive question contracts used by the ask_user tool."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

MAX_QUESTIONS = 4
MAX_OPTIONS = 10


@dataclass(slots=True, frozen=True)
class QuestionOption:
    label: str
    description: str = ""


@dataclass(slots=True, frozen=True)
class Question:
    id: str
    question: str
    header: str | None = None
    options: tuple[QuestionOption, ...] = ()
    multi_select: bool = False


@dataclass(slots=True, frozen=True)
class QuestionAnswer:
    id: str
    selected: tuple[str, ...] = ()
    custom: str | None = None


@dataclass(slots=True, frozen=True)
class QuestionRequest:
    call_id: str
    questions: tuple[Question, ...]


class QuestionHandler(Protocol):
    async def request(
        self,
        request: QuestionRequest,
        cancel_event: asyncio.Event | None = None,
    ) -> list[QuestionAnswer] | None:
        """Wait for the user's answers; None means cancelled or skipped."""

    async def cancel_all(self, reason: str = "ask_user was cancelled") -> None:
        """Cancel every pending question."""

    def pending(self) -> list[QuestionRequest]:
        """Return questions waiting for an answer."""


@dataclass(slots=True)
class _PendingQuestion:
    request: QuestionRequest
    future: asyncio.Future[list[QuestionAnswer] | None]


class QuestionManager:
    """Track pending ask_user calls and their answers."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingQuestion] = {}

    def open(
        self,
        request: QuestionRequest,
    ) -> asyncio.Future[list[QuestionAnswer] | None]:
        existing = self._pending.get(request.call_id)
        if existing is not None and not existing.future.done():
            return existing.future
        future: asyncio.Future[list[QuestionAnswer] | None] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request.call_id] = _PendingQuestion(
            request=request,
            future=future,
        )
        return future

    async def wait(
        self,
        call_id: str,
        future: asyncio.Future[list[QuestionAnswer] | None],
        cancel_event: asyncio.Event | None,
    ) -> list[QuestionAnswer] | None:
        if cancel_event is None:
            try:
                return await future
            finally:
                self._pending.pop(call_id, None)

        if cancel_event.is_set():
            self._cancel(call_id, future)
            return None

        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {future, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                return future.result()
            self._cancel(call_id, future)
            return None
        finally:
            cancel_task.cancel()
            try:
                await cancel_task
            except (asyncio.CancelledError, Exception):
                pass
            self._pending.pop(call_id, None)

    def resolve(
        self,
        call_id: str,
        answers: list[QuestionAnswer] | None,
    ) -> bool:
        pending = self._pending.get(call_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(answers)
        self._pending.pop(call_id, None)
        return True

    def cancel_all(self, reason: str = "ask_user was cancelled") -> None:
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result(None)
        self._pending.clear()

    def pending(self) -> list[QuestionRequest]:
        return [
            pending.request
            for pending in self._pending.values()
            if not pending.future.done()
        ]

    def _cancel(
        self,
        call_id: str,
        future: asyncio.Future[list[QuestionAnswer] | None],
    ) -> None:
        if not future.done():
            future.cancel()
        self._pending.pop(call_id, None)


def normalize_questions(raw: object) -> list[Question]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty array")
    if len(raw) > MAX_QUESTIONS:
        raise ValueError(f"at most {MAX_QUESTIONS} questions are allowed")

    questions: list[Question] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"question {index} must be an object")

        text = item.get("question")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"question {index} must be a non-empty string")

        raw_id = item.get("id")
        if raw_id is None:
            question_id = f"q{index}"
        elif isinstance(raw_id, str) and raw_id.strip():
            question_id = raw_id.strip()
        else:
            raise ValueError(f"question {index} id must be a non-empty string")
        if question_id in seen_ids:
            raise ValueError(f"duplicate question id: {question_id}")
        seen_ids.add(question_id)

        header = item.get("header")
        if header is not None and (
            not isinstance(header, str) or not header.strip()
        ):
            raise ValueError(f"question {index} header must be a string")

        options = _normalize_options(item.get("options"), index)
        multi_select = item.get("multi_select", False)
        if not isinstance(multi_select, bool):
            raise ValueError(
                f"question {index} multi_select must be a boolean"
            )

        questions.append(
            Question(
                id=question_id,
                question=text.strip(),
                header=header.strip() if isinstance(header, str) else None,
                options=tuple(options),
                multi_select=multi_select,
            )
        )
    return questions


def normalize_answers(
    request: QuestionRequest,
    raw: object,
) -> list[QuestionAnswer]:
    if not isinstance(raw, list):
        raise ValueError("answers must be an array")

    by_id: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each answer must be an object")
        answer_id = item.get("id")
        if not isinstance(answer_id, str) or not answer_id:
            raise ValueError("answer id must be a non-empty string")
        if answer_id in by_id:
            raise ValueError(f"duplicate answer id: {answer_id}")
        by_id[answer_id] = item

    known = {question.id: question for question in request.questions}
    unknown = set(by_id) - set(known)
    if unknown:
        raise ValueError(
            "unknown answer ids: " + ", ".join(sorted(unknown))
        )

    answers: list[QuestionAnswer] = []
    for question in request.questions:
        item = by_id.get(question.id, {})
        raw_selected = item.get("selected", [])
        if not isinstance(raw_selected, list) or not all(
            isinstance(value, str) for value in raw_selected
        ):
            raise ValueError(
                f"answer {question.id} selected must be an array of strings"
            )
        allowed = {option.label for option in question.options}
        invalid = [value for value in raw_selected if value not in allowed]
        if invalid:
            raise ValueError(
                f"answer {question.id} has unknown options: "
                + ", ".join(invalid)
            )
        if not question.multi_select and len(raw_selected) > 1:
            raise ValueError(
                f"answer {question.id} allows only one selection"
            )

        custom = item.get("custom")
        if custom is not None and not isinstance(custom, str):
            raise ValueError(
                f"answer {question.id} custom must be a string"
            )

        answers.append(
            QuestionAnswer(
                id=question.id,
                selected=tuple(raw_selected),
                custom=custom.strip() if custom and custom.strip() else None,
            )
        )
    return answers


def question_request_to_dict(request: QuestionRequest) -> dict[str, Any]:
    return {
        "callId": request.call_id,
        "questions": [
            {
                "id": question.id,
                "question": question.question,
                **(
                    {"header": question.header}
                    if question.header is not None
                    else {}
                ),
                "options": [
                    {
                        "label": option.label,
                        "description": option.description,
                    }
                    for option in question.options
                ],
                "multiSelect": question.multi_select,
            }
            for question in request.questions
        ],
    }


def answer_to_dict(answer: QuestionAnswer) -> dict[str, Any]:
    return {
        "id": answer.id,
        "selected": list(answer.selected),
        **({"custom": answer.custom} if answer.custom is not None else {}),
    }


def _normalize_options(
    raw: object,
    index: int,
) -> list[QuestionOption]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"question {index} options must be an array")
    if len(raw) > MAX_OPTIONS:
        raise ValueError(
            f"question {index} allows at most {MAX_OPTIONS} options"
        )

    options: list[QuestionOption] = []
    seen: set[str] = set()
    for option in raw:
        if isinstance(option, str):
            label = option.strip()
            description = ""
        elif isinstance(option, dict):
            raw_label = option.get("label")
            if not isinstance(raw_label, str) or not raw_label.strip():
                raise ValueError(
                    f"question {index} option label must be a string"
                )
            label = raw_label.strip()
            raw_description = option.get("description", "")
            if not isinstance(raw_description, str):
                raise ValueError(
                    f"question {index} option description must be a string"
                )
            description = raw_description.strip()
        else:
            raise ValueError(
                f"question {index} options must be strings or objects"
            )

        if not label:
            raise ValueError(f"question {index} option label is empty")
        if label in seen:
            raise ValueError(
                f"question {index} has duplicate option: {label}"
            )
        seen.add(label)
        options.append(
            QuestionOption(label=label, description=description)
        )
    return options


__all__ = [
    "MAX_OPTIONS",
    "MAX_QUESTIONS",
    "Question",
    "QuestionAnswer",
    "QuestionHandler",
    "QuestionManager",
    "QuestionOption",
    "QuestionRequest",
    "answer_to_dict",
    "normalize_answers",
    "normalize_questions",
    "question_request_to_dict",
]

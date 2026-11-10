"""RPC bridge for interactive ask_user questions."""

from __future__ import annotations

import asyncio

from ling_agent.questions import (
    QuestionAnswer,
    QuestionHandler,
    QuestionManager,
    QuestionRequest,
    answer_to_dict,
    normalize_answers,
    question_request_to_dict,
)

from .ui_events import EmitUiEvent


class RpcQuestionHandler(QuestionHandler):
    """Wait for `question.answer` RPCs and emit UI question events."""

    def __init__(self, emit: EmitUiEvent) -> None:
        self._emit = emit
        self._manager = QuestionManager()
        self._requests: dict[str, QuestionRequest] = {}

    async def request(
        self,
        request: QuestionRequest,
        cancel_event: asyncio.Event | None = None,
    ) -> list[QuestionAnswer] | None:
        self._requests[request.call_id] = request
        future = self._manager.open(request)
        await self._emit(
            {
                "type": "question.requested",
                "question": question_request_to_dict(request),
            }
        )
        try:
            answers = await self._manager.wait(
                request.call_id,
                future,
                cancel_event,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._emit(
                    {
                        "type": "question.resolved",
                        "callId": request.call_id,
                        "answers": [],
                        "cancelled": True,
                    }
                )
            )
            raise
        finally:
            self._requests.pop(request.call_id, None)

        await self._emit(
            {
                "type": "question.resolved",
                "callId": request.call_id,
                "answers": (
                    [answer_to_dict(answer) for answer in answers]
                    if answers is not None
                    else []
                ),
                "cancelled": answers is None,
            }
        )
        return answers

    async def resolve(
        self,
        call_id: str,
        raw_answers: object = None,
        *,
        cancelled: bool = False,
    ) -> bool:
        request = self._requests.get(call_id)
        if request is None:
            return False
        if cancelled:
            return self._manager.resolve(call_id, None)
        answers = normalize_answers(request, raw_answers)
        return self._manager.resolve(call_id, answers)

    async def cancel_all(
        self,
        reason: str = "ask_user was cancelled",
    ) -> None:
        self._manager.cancel_all(reason)

    def pending(self) -> list[QuestionRequest]:
        return self._manager.pending()


__all__ = ["RpcQuestionHandler"]

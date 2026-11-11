"""Tests for comment target analysis, prompts, and RPC generation."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ling_ai.events import DoneEvent, ErrorEvent
from ling_ai.stream import AssistantMessageEventStream, StreamOptions
from ling_ai.types import AssistantMessage, Context, Model, TextContent, Usage
from ling_comment.pipeline import generate_comment
from ling_comment.prompt import build_user_prompt
from ling_comment.targets import (
    CommentError,
    CommentTarget,
    analyze_target,
)
from ling_rpc.comments import CommentService
from ling_rpc.server import JsonRpcServer
from ling_rpc.sessions import SessionService

MODEL = Model(
    id="test-model",
    provider="test",
    api="openai-completions",
)


def _target(
    source: str,
    *,
    name: str = "calc",
    kind: str = "function",
    start_line: int = 0,
    language_id: str = "python",
) -> CommentTarget:
    return CommentTarget(
        path="demo.py",
        language_id=language_id,
        symbol_name=name,
        symbol_kind=kind,
        start_line=start_line,
        source_text=source,
    )


def _message(
    text: str,
    *,
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text)] if text else [],
        provider=MODEL.provider,
        model=MODEL.id,
        usage=Usage(),
        stop_reason=stop_reason,
        timestamp=1,
        error_message=error_message,
    )


class StaticStream:
    def __init__(self, message: AssistantMessage) -> None:
        self.message = message
        self.contexts: list[Context] = []
        self.options: list[StreamOptions] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        options: StreamOptions,
    ) -> AssistantMessageEventStream:
        self.contexts.append(context)
        self.options.append(options)
        stream = AssistantMessageEventStream()
        if self.message.stop_reason == "error":
            stream.push(ErrorEvent(reason="error", error=self.message))
        else:
            stream.push(
                DoneEvent(
                    reason="stop",
                    message=self.message,
                )
            )
        stream.end()
        return stream


FUNCTION_SOURCE = '''@cache
def calc(
    items: list[str],
    rate: float,
    discount=0.0,
    *args,
    flag: bool = True,
    **kwargs,
) -> float:
    if not items:
        raise ValueError("empty items")
    return 1.0
'''


class CommentTargetTests(unittest.TestCase):
    def test_function_analysis_extracts_structured_context(self) -> None:
        analysis = analyze_target(_target(FUNCTION_SOURCE))

        self.assertIn("def calc(", analysis.signature)
        self.assertIn("rate: float", analysis.signature)
        self.assertIn("-> float", analysis.signature)
        self.assertEqual(
            [parameter.name for parameter in analysis.parameters],
            ["items", "rate", "discount", "*args", "flag", "**kwargs"],
        )
        self.assertEqual(analysis.parameters[0].annotation, "list[str]")
        self.assertEqual(analysis.parameters[2].default, "0.0")
        self.assertEqual(analysis.returns, "float")
        self.assertEqual(analysis.raises, ("ValueError",))
        self.assertEqual(analysis.decorators, ("cache",))
        self.assertIsNone(analysis.existing_docstring)
        self.assertEqual(analysis.placement.line, 9)
        self.assertEqual(analysis.placement.indent, "    ")

    def test_method_analysis_keeps_absolute_line_and_indent(self) -> None:
        source = (
            "    def run(self, value: str) -> None:\n"
            "        return None\n"
        )
        analysis = analyze_target(
            _target(
                source,
                name="run",
                kind="method",
                start_line=10,
            )
        )

        self.assertEqual(
            [parameter.name for parameter in analysis.parameters],
            ["self", "value"],
        )
        self.assertEqual(analysis.placement.line, 11)
        self.assertEqual(analysis.placement.indent, "        ")

    def test_decorated_method_with_indentation(self) -> None:
        source = (
            "    @cache\n"
            "    def run(self) -> None:\n"
            "        return None\n"
        )
        analysis = analyze_target(
            _target(
                source,
                name="run",
                kind="method",
                start_line=5,
            )
        )

        self.assertEqual(analysis.decorators, ("cache",))
        self.assertEqual(analysis.placement.line, 7)
        self.assertEqual(analysis.placement.indent, "        ")

    def test_async_function_is_supported(self) -> None:
        source = (
            "async def load(path: str) -> str:\n"
            "    return path\n"
        )
        analysis = analyze_target(_target(source, name="load"))

        self.assertTrue(analysis.signature.startswith("async def load("))
        self.assertEqual(analysis.returns, "str")

    def test_class_target_has_no_parameters(self) -> None:
        source = (
            "class Service(Base):\n"
            "    def run(self) -> None:\n"
            "        return None\n"
        )
        analysis = analyze_target(
            _target(source, name="Service", kind="class")
        )

        self.assertEqual(analysis.signature, "class Service(Base):")
        self.assertEqual(analysis.parameters, ())
        self.assertIsNone(analysis.returns)
        self.assertEqual(analysis.placement.indent, "    ")

    def test_nested_raises_are_ignored(self) -> None:
        source = (
            "def outer():\n"
            "    def inner():\n"
            "        raise RuntimeError('inner')\n"
            "    raise ValueError('outer')\n"
        )
        analysis = analyze_target(_target(source, name="outer"))

        self.assertEqual(analysis.raises, ("ValueError",))

    def test_existing_docstring_is_rejected(self) -> None:
        source = (
            "def calc():\n"
            '    """Existing."""\n'
            "    return 1\n"
        )
        with self.assertRaises(CommentError) as context:
            analyze_target(_target(source))

        self.assertEqual(context.exception.reason, "already_commented")

    def test_one_line_body_is_rejected(self) -> None:
        with self.assertRaises(CommentError) as context:
            analyze_target(_target("def calc(): return 1\n"))

        self.assertEqual(context.exception.reason, "unsupported_layout")

    def test_unsupported_language_is_rejected(self) -> None:
        with self.assertRaises(CommentError) as context:
            analyze_target(
                _target(
                    "function calc() { return 1; }\n",
                    language_id="typescript",
                )
            )

        self.assertEqual(
            context.exception.reason,
            "unsupported_language",
        )


class CommentPromptTests(unittest.TestCase):
    def test_prompt_includes_structured_context(self) -> None:
        target = _target(FUNCTION_SOURCE)
        prompt = build_user_prompt(target, analyze_target(target))

        self.assertIn("Signature: def calc(", prompt)
        self.assertIn("Parameters:", prompt)
        self.assertIn("- rate: float", prompt)
        self.assertIn("Returns: float", prompt)
        self.assertIn("Raises: ValueError", prompt)
        self.assertIn("Decorators: cache", prompt)
        self.assertIn("<source>", prompt)


class CommentPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_strips_markdown_fence(self) -> None:
        stream = StaticStream(
            _message('```python\n"""Adds two numbers."""\n```')
        )

        result = await generate_comment(
            _target(FUNCTION_SOURCE),
            model=MODEL,
            stream_fn=stream,
        )

        self.assertEqual(result.comment, '"""Adds two numbers."""')
        self.assertEqual(result.placement.line, 9)
        self.assertEqual(stream.options[0].temperature, 0.2)
        self.assertIn(
            "Write the docstring.",
            stream.contexts[0].messages[0].content,
        )

    async def test_wraps_plain_text_in_docstring_quotes(self) -> None:
        stream = StaticStream(_message("Adds two numbers."))

        result = await generate_comment(
            _target(FUNCTION_SOURCE),
            model=MODEL,
            stream_fn=stream,
        )

        self.assertEqual(result.comment, '"""Adds two numbers."""')

    async def test_empty_response_is_rejected(self) -> None:
        stream = StaticStream(_message("   "))

        with self.assertRaises(CommentError) as context:
            await generate_comment(
                _target(FUNCTION_SOURCE),
                model=MODEL,
                stream_fn=stream,
            )

        self.assertEqual(context.exception.reason, "empty_response")

    async def test_model_error_is_surfaced(self) -> None:
        stream = StaticStream(
            _message(
                "",
                stop_reason="error",
                error_message="provider down",
            )
        )

        with self.assertRaises(CommentError) as context:
            await generate_comment(
                _target(FUNCTION_SOURCE),
                model=MODEL,
                stream_fn=stream,
            )

        self.assertEqual(context.exception.reason, "model_error")
        self.assertEqual(str(context.exception), "provider down")


class CommentRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_comment_generate_returns_comment_and_placement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            sessions = SessionService(
                emit,
                model=MODEL,
                stream_fn=StaticStream(_message("unused")),
                storage_root=Path(temp) / "state",
            )
            comments = CommentService(
                model=MODEL,
                stream_fn=StaticStream(
                    _message('"""Adds two numbers."""')
                ),
            )
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=sessions,
                comments=comments,
            )

            try:
                response = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "comment.generate",
                        "params": {
                            "path": "demo.py",
                            "languageId": "python",
                            "symbolName": "calc",
                            "symbolKind": "function",
                            "startLine": 0,
                            "sourceText": FUNCTION_SOURCE,
                        },
                    }
                )
            finally:
                await sessions.close()

            self.assertNotIn("error", response)
            self.assertEqual(
                response["result"]["comment"],
                '"""Adds two numbers."""',
            )
            self.assertEqual(
                response["result"]["placement"],
                {"line": 9, "indent": "    "},
            )

    async def test_comment_generate_rejects_invalid_params(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            sessions = SessionService(
                emit,
                model=MODEL,
                stream_fn=StaticStream(_message("unused")),
                storage_root=Path(temp) / "state",
            )
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=sessions,
            )

            try:
                response = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "comment.generate",
                        "params": {
                            "path": "demo.py",
                            "languageId": "python",
                            "symbolName": "calc",
                            "symbolKind": "function",
                            "startLine": 0,
                        },
                    }
                )
            finally:
                await sessions.close()

            self.assertEqual(response["error"]["code"], -32602)

    async def test_comment_generate_reports_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            events: list[dict[str, Any]] = []

            async def emit(event: dict[str, Any]) -> None:
                events.append(event)

            sessions = SessionService(
                emit,
                model=MODEL,
                stream_fn=StaticStream(_message("unused")),
                storage_root=Path(temp) / "state",
            )
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=sessions,
                comments=CommentService(
                    model=MODEL,
                    stream_fn=StaticStream(_message('"""x"""')),
                ),
            )

            try:
                response = await server.handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "comment.generate",
                        "params": {
                            "path": "demo.py",
                            "languageId": "python",
                            "symbolName": "calc",
                            "symbolKind": "function",
                            "startLine": 0,
                            "sourceText": (
                                "def calc():\n"
                                '    """Existing."""\n'
                                "    return 1\n"
                            ),
                        },
                    }
                )
            finally:
                await sessions.close()

            self.assertEqual(response["error"]["code"], -32001)
            self.assertEqual(
                response["error"]["data"]["reason"],
                "already_commented",
            )


if __name__ == "__main__":
    unittest.main()

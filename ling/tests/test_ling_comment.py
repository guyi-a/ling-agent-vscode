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
from ling_comment.prompt import build_user_prompt, system_prompt
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


TYPESCRIPT_SOURCE = (
    "export function add(a: number, b: number): number {\n"
    "  return a + b;\n"
    "}\n"
)

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
        self.assertIsNone(analysis.existing_comment)
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

    def test_existing_docstring_is_replaced(self) -> None:
        source = (
            "def calc():\n"
            '    """Existing."""\n'
            "    return 1\n"
        )
        analysis = analyze_target(_target(source))

        self.assertEqual(analysis.existing_comment, "Existing.")
        self.assertTrue(analysis.placement.replace)
        self.assertEqual(analysis.placement.line, 1)
        self.assertEqual(analysis.placement.end_line, 2)
        self.assertEqual(analysis.placement.indent, "    ")

    def test_multiline_docstring_replacement_range(self) -> None:
        source = (
            "def calc():\n"
            '    """Line one.\n'
            "\n"
            "    Line two.\n"
            '    """\n'
            "    return 1\n"
        )
        analysis = analyze_target(_target(source))

        self.assertEqual(analysis.placement.line, 1)
        self.assertEqual(analysis.placement.end_line, 5)

    def test_one_line_body_is_rejected(self) -> None:
        with self.assertRaises(CommentError) as context:
            analyze_target(_target("def calc(): return 1\n"))

        self.assertEqual(context.exception.reason, "unsupported_layout")

    def test_unsupported_language_is_rejected(self) -> None:
        with self.assertRaises(CommentError) as context:
            analyze_target(
                _target(
                    "def calc():\n    return 1\n",
                    language_id="ruby",
                )
            )

        self.assertEqual(
            context.exception.reason,
            "unsupported_language",
        )

    def test_typescript_function_analysis(self) -> None:
        source = (
            "export function calc(\n"
            "  items: string[],\n"
            "  rate: number = 0.1,\n"
            "  options?: { strict: boolean },\n"
            "): number {\n"
            "  return 0;\n"
            "}\n"
        )
        analysis = analyze_target(
            _target(source, language_id="typescript")
        )

        self.assertIn("export function calc(", analysis.signature)
        self.assertIn("): number", analysis.signature)
        self.assertEqual(
            [parameter.name for parameter in analysis.parameters],
            ["items", "rate", "options"],
        )
        self.assertEqual(
            analysis.parameters[0].annotation,
            "string[]",
        )
        self.assertEqual(analysis.parameters[1].default, "0.1")
        self.assertEqual(
            analysis.parameters[2].annotation,
            "{ strict: boolean }",
        )
        self.assertEqual(analysis.returns, "number")
        self.assertEqual(analysis.raises, ())
        self.assertEqual(analysis.placement.line, 0)
        self.assertEqual(analysis.placement.indent, "")

    def test_typescript_method_placement(self) -> None:
        source = (
            "    async run(value: string): Promise<void> {\n"
            "        return;\n"
            "    }\n"
        )
        analysis = analyze_target(
            _target(
                source,
                name="run",
                kind="method",
                start_line=10,
                language_id="typescript",
            )
        )

        self.assertIn("async run(value: string)", analysis.signature)
        self.assertEqual(analysis.returns, "Promise<void>")
        self.assertEqual(analysis.placement.line, 10)
        self.assertEqual(analysis.placement.indent, "    ")

    def test_typescript_decorated_method(self) -> None:
        source = (
            "    @logged\n"
            "    @cached\n"
            "    async run(): Promise<void> {\n"
            "        return;\n"
            "    }\n"
        )
        analysis = analyze_target(
            _target(
                source,
                name="run",
                kind="method",
                start_line=5,
                language_id="typescript",
            )
        )

        self.assertEqual(
            analysis.decorators,
            ("@logged", "@cached"),
        )
        self.assertEqual(analysis.placement.line, 5)
        self.assertEqual(analysis.placement.indent, "    ")

    def test_typescript_leading_jsdoc_is_replaced(self) -> None:
        source = (
            "/**\n"
            " * Old.\n"
            " */\n"
            "export function add(a: number): number {\n"
            "  return a;\n"
            "}\n"
        )
        analysis = analyze_target(
            _target(source, name="add", language_id="typescript")
        )

        self.assertIn("Old.", analysis.existing_comment or "")
        self.assertTrue(analysis.placement.replace)
        self.assertEqual(analysis.placement.line, 0)
        self.assertEqual(analysis.placement.end_line, 3)
        self.assertEqual(
            analysis.signature,
            "export function add(a: number): number",
        )

    def test_typescript_class_analysis(self) -> None:
        source = (
            "export class Service<T> extends Base "
            "implements Runner {\n"
            "  run(): void {}\n"
            "}\n"
        )
        analysis = analyze_target(
            _target(
                source,
                name="Service",
                kind="class",
                language_id="typescript",
            )
        )

        self.assertEqual(
            analysis.signature,
            "export class Service<T> extends Base implements Runner",
        )
        self.assertEqual(analysis.parameters, ())
        self.assertIsNone(analysis.returns)

    def test_typescript_arrow_function(self) -> None:
        source = (
            "export const add = (a: number, b: number = 1) "
            "=> a + b;\n"
        )
        analysis = analyze_target(
            _target(
                source,
                name="add",
                language_id="typescript",
            )
        )

        self.assertIn("export const add =", analysis.signature)
        self.assertEqual(
            [parameter.name for parameter in analysis.parameters],
            ["a", "b"],
        )
        self.assertEqual(analysis.parameters[1].default, "1")
        self.assertIsNone(analysis.returns)


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

    def test_prompt_includes_existing_comment(self) -> None:
        source = (
            "def calc():\n"
            '    """Old."""\n'
            "    return 1\n"
        )
        target = _target(source)
        prompt = build_user_prompt(target, analyze_target(target))

        self.assertIn("Existing comment:", prompt)
        self.assertIn("Old.", prompt)
        self.assertIn("Rewrite it", prompt)

    def test_prompt_selects_format_by_language(self) -> None:
        target = _target(TYPESCRIPT_SOURCE, name="add", language_id="typescript")
        prompt = build_user_prompt(target, analyze_target(target))

        self.assertIn("Language: typescript", prompt)
        self.assertIn("- a: number", prompt)
        self.assertIn("Returns: number", prompt)
        self.assertIn("JSDoc", system_prompt("typescript"))
        self.assertIn("docstring", system_prompt("python"))


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

    async def test_wraps_plain_text_in_jsdoc(self) -> None:
        stream = StaticStream(_message("Adds two numbers."))

        result = await generate_comment(
            _target(
                TYPESCRIPT_SOURCE,
                name="add",
                language_id="typescript",
            ),
            model=MODEL,
            stream_fn=stream,
        )

        self.assertEqual(
            result.comment,
            "/**\n * Adds two numbers.\n */",
        )
        self.assertEqual(result.placement.line, 0)
        self.assertEqual(result.placement.indent, "")

    async def test_keeps_existing_jsdoc(self) -> None:
        comment = "/**\n * Adds two numbers.\n */"
        stream = StaticStream(_message(comment))

        result = await generate_comment(
            _target(
                TYPESCRIPT_SOURCE,
                name="add",
                language_id="typescript",
            ),
            model=MODEL,
            stream_fn=stream,
        )

        self.assertEqual(result.comment, comment)

    async def test_strips_star_prefixes_before_wrapping(self) -> None:
        stream = StaticStream(_message("* Adds.\n* More."))

        result = await generate_comment(
            _target(
                TYPESCRIPT_SOURCE,
                name="add",
                language_id="typescript",
            ),
            model=MODEL,
            stream_fn=stream,
        )

        self.assertEqual(
            result.comment,
            "/**\n * Adds.\n * More.\n */",
        )

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

    async def test_comment_generate_replaces_existing_docstring(
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
            server = JsonRpcServer(
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                sessions=sessions,
                comments=CommentService(
                    model=MODEL,
                    stream_fn=StaticStream(_message('"""Updated."""')),
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

            self.assertNotIn("error", response)
            self.assertEqual(
                response["result"]["comment"],
                '"""Updated."""',
            )
            self.assertEqual(
                response["result"]["placement"],
                {"line": 1, "indent": "    ", "endLine": 2},
            )


if __name__ == "__main__":
    unittest.main()

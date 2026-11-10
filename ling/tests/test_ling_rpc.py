"""Tests for the Ling JSON-RPC sidecar process."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from ling_rpc.server import PROTOCOL_VERSION


class LingRpcProcessTests(unittest.TestCase):
    def start_server(self) -> subprocess.Popen[str]:
        project_root = Path(__file__).resolve().parents[1]
        return subprocess.Popen(
            [sys.executable, "-m", "ling_rpc"],
            cwd=project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def request(
        self,
        process: subprocess.Popen[str],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        process.stdin.flush()
        line = process.stdout.readline()
        self.assertTrue(line, "sidecar closed stdout without a response")
        return json.loads(line)

    def test_initialize_ping_and_shutdown(self) -> None:
        process = self.start_server()
        try:
            initialized = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": PROTOCOL_VERSION},
                },
            )
            self.assertEqual(initialized["id"], 1)
            self.assertEqual(
                initialized["result"]["protocolVersion"],
                PROTOCOL_VERSION,
            )
            self.assertEqual(
                initialized["result"]["serverInfo"]["name"],
                "ling-sidecar",
            )

            pinged = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "ping",
                    "params": {"nonce": "test"},
                },
            )
            self.assertEqual(
                pinged["result"],
                {"pong": True, "nonce": "test"},
            )

            stopped = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "shutdown",
                },
            )
            self.assertIsNone(stopped["result"])
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            self.close_process(process)

    def test_protocol_and_method_errors(self) -> None:
        process = self.start_server()
        try:
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write("{broken json\n")
            process.stdin.flush()
            parse_error = json.loads(process.stdout.readline())
            self.assertEqual(parse_error["error"]["code"], -32700)

            missing = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "missing",
                },
            )
            self.assertEqual(missing["error"]["code"], -32601)

            bad_question = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "question.answer",
                    "params": {},
                },
            )
            self.assertEqual(bad_question["error"]["code"], -32602)

            bad_answers = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "question.answer",
                    "params": {"callId": "call-1"},
                },
            )
            self.assertEqual(bad_answers["error"]["code"], -32602)

            unknown = self.request(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "question.answer",
                    "params": {"callId": "call-1", "answers": []},
                },
            )
            self.assertEqual(unknown["result"], {"resolved": False})
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            self.close_process(process)

    def close_process(self, process: subprocess.Popen[str]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


if __name__ == "__main__":
    unittest.main()

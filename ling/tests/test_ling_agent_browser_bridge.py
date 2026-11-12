"""Tests for the Kro browser bridge server."""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from ling_agent.browser_bridge import (
    BrowserBridge,
    BrowserBridgeError,
    PING_SIGNATURE,
    PING_SIG_HEADER,
    WS_CLOSE_AUTH_FAILED,
)


class BrowserBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bridge = BrowserBridge(port=0, command_timeout=1.0)
        await self.bridge.ensure_started()
        self.port = self.bridge.bound_port
        self.ws = await connect(self._ws_url())

    async def asyncTearDown(self) -> None:
        await self.ws.close()
        await self.bridge.stop()

    def _ws_url(self) -> str:
        return (
            f"ws://127.0.0.1:{self.port}/chrome-bridge/ws"
            f"?token={self.bridge.registry.discovery_token}"
        )

    async def _hello(self) -> None:
        await self.ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "id": "hello-1",
                    "payload": {
                        "browser_id": "browser-1",
                        "browser_label": "Edge",
                        "client": {"extension_version": "0.0.6"},
                    },
                }
            )
        )
        ack = json.loads(await self.ws.recv())
        self.assertEqual(ack["type"], "hello_ack")
        self.assertEqual(ack["payload"]["browser_id"], "browser-1")

    async def test_ping_signature(self) -> None:
        async with httpx.AsyncClient() as client:
            ok = await client.get(
                f"http://127.0.0.1:{self.port}/chrome-bridge/ping",
                headers={PING_SIG_HEADER: PING_SIGNATURE},
            )
            self.assertEqual(ok.status_code, 200)
            payload = ok.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["wsPath"], "/chrome-bridge/ws")
            self.assertTrue(payload["token"])

            bad = await client.get(
                f"http://127.0.0.1:{self.port}/chrome-bridge/ping"
            )
            self.assertEqual(bad.status_code, 403)

            head = await client.head(
                f"http://127.0.0.1:{self.port}/chrome-bridge/ping"
            )
            self.assertEqual(head.status_code, 200)

    async def test_bad_token_closes_with_auth_code(self) -> None:
        bad = await connect(
            f"ws://127.0.0.1:{self.port}/chrome-bridge/ws?token=nope"
        )
        with self.assertRaises(ConnectionClosed) as raised:
            await bad.recv()
        self.assertEqual(
            getattr(raised.exception.rcvd, "code", None),
            WS_CLOSE_AUTH_FAILED,
        )
        await bad.close()

    async def test_hello_pages_and_command_round_trip(self) -> None:
        await self._hello()
        self.assertTrue(self.bridge.extension_status()["ready"])

        await self.ws.send(
            json.dumps(
                {
                    "type": "event",
                    "payload": {
                        "name": "page_updated",
                        "browser_id": "browser-1",
                        "window_id": 1,
                        "tab_id": 2,
                        "url": "https://example.com",
                        "title": "Example",
                        "active": True,
                    },
                }
            )
        )
        await asyncio.sleep(0.05)
        pages = self.bridge.list_pages("browser-1")
        self.assertEqual(len(pages), 1)
        page_id = pages[0]["pageId"]

        command_task = asyncio.create_task(
            self.bridge.read_state("browser-1", page_id)
        )
        command = json.loads(await self.ws.recv())
        self.assertEqual(command["type"], "command")
        self.assertEqual(command["payload"]["tool"], "browser_read_state")
        self.assertEqual(command["payload"]["arguments"]["tab_id"], 2)

        await self.ws.send(
            json.dumps(
                {
                    "type": "result",
                    "id": command["id"],
                    "payload": {"state": "page state"},
                }
            )
        )
        self.assertEqual(await command_task, {"state": "page state"})

        await self.ws.send(
            json.dumps(
                {
                    "type": "event",
                    "payload": {
                        "name": "page_removed",
                        "browser_id": "browser-1",
                        "tab_id": 2,
                    },
                }
            )
        )
        await asyncio.sleep(0.05)
        self.assertEqual(self.bridge.list_pages("browser-1"), [])

    async def test_command_timeout(self) -> None:
        await self._hello()
        await self.ws.send(
            json.dumps(
                {
                    "type": "event",
                    "payload": {
                        "name": "page_updated",
                        "browser_id": "browser-1",
                        "window_id": 1,
                        "tab_id": 2,
                        "url": "https://example.com",
                        "title": "Example",
                        "active": True,
                    },
                }
            )
        )
        await asyncio.sleep(0.05)
        page_id = self.bridge.list_pages("browser-1")[0]["pageId"]
        self.bridge.command_timeout = 0.1
        task = asyncio.create_task(
            self.bridge.read_state("browser-1", page_id)
        )
        await self.ws.recv()  # consume the command, but never reply
        with self.assertRaises(BrowserBridgeError) as raised:
            await task
        self.assertEqual(raised.exception.code, "COMMAND_TIMEOUT")

    async def test_missing_page_and_not_connected(self) -> None:
        await self._hello()
        with self.assertRaises(BrowserBridgeError) as raised:
            await self.bridge.read_state("browser-1", "nope")
        self.assertEqual(raised.exception.code, "PAGE_NOT_FOUND")

        with self.assertRaises(BrowserBridgeError) as raised:
            await self.bridge.open_tab("other-browser", "https://example.com")
        self.assertEqual(raised.exception.code, "BROWSER_NOT_CONNECTED")

    async def test_stop_releases_port_and_is_idempotent(self) -> None:
        await self.bridge.stop()
        await self.bridge.stop()
        # A fresh bridge can take the same port.
        replacement = BrowserBridge(port=self.port, command_timeout=1.0)
        await replacement.ensure_started()
        self.assertEqual(replacement.bound_port, self.port)
        await replacement.stop()


class BrowserBridgePortTests(unittest.IsolatedAsyncioTestCase):
    async def test_port_conflict_is_actionable(self) -> None:
        first = BrowserBridge(port=0)
        await first.ensure_started()
        port = first.bound_port
        second = BrowserBridge(port=port)
        try:
            with self.assertRaises(BrowserBridgeError) as raised:
                await second.ensure_started()
            self.assertEqual(raised.exception.code, "PORT_IN_USE")
            self.assertIn("already in use", str(raised.exception))
        finally:
            await first.stop()

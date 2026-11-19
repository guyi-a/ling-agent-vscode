import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { SidecarClient } from "../rpc.js";
import type { UiEvent } from "../types.js";

const FAKE = `
import readline from "node:readline";
const rl = readline.createInterface({ input: process.stdin });
const send = (payload) => process.stdout.write(JSON.stringify(payload) + "\\n");
rl.on("line", (line) => {
  const msg = JSON.parse(line);
  const id = msg.id;
  if (msg.method === "initialize") {
    send({ jsonrpc: "2.0", id, result: {
      protocolVersion: 1,
      serverInfo: { name: "fake", version: "1" },
      capabilities: {},
    }});
  } else if (msg.method === "ping") {
    send({ jsonrpc: "2.0", id, result: {
      pong: true, nonce: msg.params?.nonce ?? null,
    }});
  } else if (msg.method === "emit") {
    send({ jsonrpc: "2.0", method: "ui.event", params: {
      event: { type: "message.delta", delta: "x" },
    }});
    send({ jsonrpc: "2.0", id, result: { ok: true } });
  } else if (msg.method === "progress") {
    send({ jsonrpc: "2.0", method: "index.progress", params: {
      phase: "indexing", files: 1,
    }});
    send({ jsonrpc: "2.0", id, result: { ok: true } });
  } else if (msg.method === "slow") {
    // Intentionally never responds.
  } else if (msg.method === "crash") {
    process.exit(3);
  } else if (msg.method === "shutdown") {
    send({ jsonrpc: "2.0", id, result: null });
    setTimeout(() => process.exit(0), 10);
  } else {
    send({ jsonrpc: "2.0", id, result: { method: msg.method } });
  }
});
`;

function createClient() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ling-rpc-test-"));
  const script = path.join(root, "fake-sidecar.mjs");
  fs.writeFileSync(script, FAKE, "utf8");
  const client = new SidecarClient({
    launch: {
      kind: "exe",
      command: process.execPath,
      args: [script],
      cwd: root,
    },
    env: process.env,
  });
  return { client, root };
}

async function cleanup(
  client: SidecarClient,
  root: string,
): Promise<void> {
  await client.close();
  fs.rmSync(root, { recursive: true, force: true });
}

test("matches responses and emits ui/index notifications", async () => {
  const { client, root } = createClient();
  try {
    await client.start();
    const initialized = await client.call<{
      protocolVersion: number;
    }>("initialize", {}, 5_000);
    assert.equal(initialized.protocolVersion, 1);
    const ping = await client.call<{ pong: boolean; nonce: string }>(
      "ping",
      { nonce: "abc" },
      5_000,
    );
    assert.deepEqual(ping, { pong: true, nonce: "abc" });

    const events: UiEvent[] = [];
    const unsubscribe = client.onEvent((event) => events.push(event));
    const progress: unknown[] = [];
    client.on("index-progress", (value) => progress.push(value));

    await client.call("emit", {}, 5_000);
    await client.call("progress", {}, 5_000);
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "message.delta");
    assert.equal(progress.length, 1);
    unsubscribe();
  } finally {
    await cleanup(client, root);
  }
});

test("rejects a request on timeout", async () => {
  const { client, root } = createClient();
  try {
    await client.start();
    await assert.rejects(
      client.call("slow", {}, 120),
      /timed out/,
    );
  } finally {
    await cleanup(client, root);
  }
});

test("rejects pending requests when the sidecar exits", async () => {
  const { client, root } = createClient();
  try {
    await client.start();
    await assert.rejects(
      client.call("crash", {}, 5_000),
      /exited/,
    );
  } finally {
    await cleanup(client, root);
  }
});

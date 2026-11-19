import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { render } from "ink-testing-library";

import {
  buildSidecarEnv,
  loadConfig,
  resolveSidecar,
} from "../dist/config.js";
import { SidecarClient } from "../dist/rpc.js";
import { App } from "../dist/tui/App.js";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");
const root = path.join(
  repoRoot,
  "ling",
  "build",
  "tui-probe",
  "tui-smoke",
);
const stateDir = path.join(root, "state");
const workspace = path.join(root, "workspace");

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(check, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = check();
    if (value) {
      return value;
    }
    await delay(200);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function main() {
  fs.rmSync(root, { recursive: true, force: true });
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(workspace, { recursive: true });
  const mcpPath = path.join(root, "mcp.json");
  fs.writeFileSync(mcpPath, JSON.stringify({ mcpServers: {} }), "utf8");

  const config = loadConfig();
  const options = {
    workspace,
    mode: "manual",
    effort: "low",
    resume: false,
    check: false,
    noColor: true,
    help: false,
    version: false,
  };
  const env = buildSidecarEnv(config, {
    ...options,
    storageRoot: stateDir,
  });
  env.LING_GLOBAL_MEMORY_ROOT = path.join(root, "global-memory");
  env.LING_MCP_CONFIG = mcpPath;
  env.LING_MCP_STATE_ROOT = path.join(root, "mcp");
  env.LING_COMPUTER_ENABLED = "0";

  const client = new SidecarClient({
    launch: resolveSidecar(options),
    env,
  });
  await client.start();
  let app;
  try {
    const initialized = await client.call(
      "initialize",
      {
        protocolVersion: 1,
        clientInfo: { name: "tui-smoke", version: "0.0.0" },
      },
      20_000,
    );
    if (initialized.protocolVersion !== 1) {
      throw new Error(`protocol ${initialized.protocolVersion}`);
    }
    const snapshot = await client.call(
      "session.snapshot",
      {},
      20_000,
    );
    app = render(
      React.createElement(App, {
        client,
        workspace,
        model: config.model ?? "unknown",
        initialMode: "manual",
        initialEffort: "low",
        initialSnapshot: snapshot,
      }),
      { exitOnCtrlC: false, patchConsole: false },
    );
    await delay(300);
    app.stdin.write(
      "Reply with exactly the single word APPLE and nothing else. " +
        "Do not use any tools.",
    );
    await delay(100);
    app.stdin.write("\r");

    await waitFor(
      () => /Ling\s+APPLE/.test(app.lastFrame() ?? ""),
      120_000,
      "assistant answer",
    );
    await waitFor(
      () => /·\s*idle/.test(app.lastFrame() ?? ""),
      30_000,
      "idle phase",
    );

    // Use a clean session so the earlier "Do not use any tools"
    // instruction cannot influence the approval probe.
    app.stdin.write("/new");
    await delay(100);
    app.stdin.write("\r");
    await waitFor(
      () => !/APPLE/.test(app.lastFrame() ?? ""),
      30_000,
      "new session",
    );

    const approvedFile = path.join(workspace, "tui_allow.txt");
    fs.rmSync(approvedFile, { force: true });
    app.stdin.write(
      "Use the write_file tool to create tui_allow.txt with exactly the " +
        "content TUI_ALLOW. Do not use any other tool.",
    );
    await delay(100);
    app.stdin.write("\r");
    await waitFor(
      () => /Approval required/.test(app.lastFrame() ?? ""),
      120_000,
      "approval card",
    );
    app.stdin.write("1");
    await waitFor(
      () => fs.existsSync(approvedFile),
      60_000,
      "approved file",
    );
    await waitFor(
      () => /·\s*idle/.test(app.lastFrame() ?? ""),
      60_000,
      "idle after approval",
    );
    if (fs.readFileSync(approvedFile, "utf8") !== "TUI_ALLOW") {
      throw new Error("approved file content mismatch");
    }

    app.stdin.write("/last");
    await delay(100);
    app.stdin.write("\r");
    await waitFor(
      () => /\+ TUI_ALLOW/.test(app.lastFrame() ?? ""),
      30_000,
      "expanded tool diff",
    );

    const current = await client.call("session.snapshot", {}, 20_000);
    if (!current.sessionId) {
      throw new Error("missing session id");
    }
    app.stdin.write("/history");
    await delay(100);
    app.stdin.write("\r");
    await waitFor(
      () => /History/.test(app.lastFrame() ?? ""),
      30_000,
      "history overlay",
    );
    app.stdin.write(`/open ${current.sessionId}`);
    await delay(100);
    app.stdin.write("\r");
    await waitFor(
      () => /Opened/.test(app.lastFrame() ?? ""),
      30_000,
      "opened session",
    );

    console.log("[tui-smoke] PASS (chat + approval + tool diff + history)");
    console.log(app.lastFrame());
  } catch (error) {
    console.error(
      `[tui-smoke] FAIL: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
    console.error(app?.lastFrame() ?? "(no frame)");
    console.error(client.stderrTail());
    process.exitCode = 1;
  } finally {
    app?.unmount();
    await client.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

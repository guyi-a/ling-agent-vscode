import React from "react";
import { render } from "ink";

import {
  buildSidecarEnv,
  requireApiKey,
  resolveSidecar,
  type CliOptions,
  type LingConfig,
} from "../config.js";
import { SidecarClient } from "../rpc.js";
import type { InitializeResult, SessionSnapshot } from "../types.js";
import { App } from "./App.js";

function makeRedactor(secret: string): (text: string) => string {
  if (!secret) {
    return (text) => text;
  }
  return (text) => text.split(secret).join("<redacted>");
}

export async function runTui(
  config: LingConfig,
  options: CliOptions,
): Promise<void> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error(
      "The interactive TUI requires a TTY. Use `ling --check` for " +
        "non-interactive verification.",
    );
  }

  const apiKey = requireApiKey(config);
  const launch = resolveSidecar(options);
  const env = buildSidecarEnv(config, options);
  const client = new SidecarClient({
    launch,
    env,
    debugLog: options.debugLog,
    redact: makeRedactor(apiKey),
  });

  await client.start();
  try {
    const initialized = await client.call<InitializeResult>(
      "initialize",
      {
        protocolVersion: 1,
        clientInfo: { name: "ling-cli", version: "0.1.0" },
      },
      20_000,
    );
    if (initialized.protocolVersion !== 1) {
      throw new Error(
        `Unsupported sidecar protocol: ${initialized.protocolVersion}`,
      );
    }

    if (options.resume) {
      const listed = await client.call<{
        sessions?: Array<{ id: string }>;
      }>(
        "session.list",
        { workspaceRoot: options.workspace },
        20_000,
      );
      const latest = listed.sessions?.[0];
      if (latest) {
        await client.call(
          "session.open",
          {
            sessionId: latest.id,
            workspaceRoot: options.workspace,
          },
          20_000,
        );
      }
    }

    const snapshot = await client.call<SessionSnapshot>(
      "session.snapshot",
      {},
      20_000,
    );

    const instance = render(
      <App
        client={client}
        workspace={options.workspace}
        model={options.model ?? config.model ?? "unknown"}
        initialMode={snapshot.approvalMode ?? options.mode}
        initialEffort={options.effort}
        initialSnapshot={snapshot}
      />,
      {
        exitOnCtrlC: false,
        patchConsole: false,
      },
    );
    await instance.waitUntilExit();
  } finally {
    await client.close();
  }
}

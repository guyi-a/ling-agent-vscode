import fs from "node:fs";
import path from "node:path";

import {
  buildSidecarEnv,
  defaultConfigPath,
  loadConfig,
  loadConfigIfExists,
  packageRoot,
  packageVersion,
  resolveSidecar,
  requireApiKey,
  type ApiProtocol,
  type ApprovalMode,
  type CliOptions,
  type Effort,
} from "./config.js";
import { SidecarClient } from "./rpc.js";
import type { InitializeResult, SessionSnapshot } from "./types.js";

const USAGE = `Ling terminal client

Usage:
  ling [options]

Options:
  -w, --workspace PATH     Workspace to open (default: current directory)
      --resume, --continue Open the most recent session in this workspace
      --mode MODE          manual | accept-write | auto | plan
      --model NAME         Override the configured model
      --effort LEVEL       low | medium | high | xhigh | max
      --api PROTOCOL       openai-completions | anthropic-messages
      --base-url URL       Override the configured API base URL
      --storage-root PATH  Use a separate Ling state root (debug only)
      --debug-log PATH     Write sanitized NDJSON diagnostics
      --python PATH        Python interpreter for a source sidecar
      --check              Start the sidecar, handshake, and exit
      --no-color           Disable color output
  -h, --help               Show this help
  -v, --version            Show the version

Run \`ling\` in a workspace to open the terminal chat. Use \`ling --check\`
to verify the sidecar and configuration without calling the model.`;

interface ParsedArgs {
  workspace?: string;
  mode?: string;
  model?: string;
  effort?: string;
  api?: string;
  baseUrl?: string;
  storageRoot?: string;
  debugLog?: string;
  python?: string;
  resume: boolean;
  check: boolean;
  noColor: boolean;
  help: boolean;
  version: boolean;
}

function parseArgs(argv: string[]): ParsedArgs {
  const parsed: ParsedArgs = {
    resume: false,
    check: false,
    noColor: false,
    help: false,
    version: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const value = (): string => {
      const next = argv[index + 1];
      if (next === undefined) {
        throw new Error(`Missing value for ${arg}.`);
      }
      index += 1;
      return next;
    };
    switch (arg) {
      case "-w":
      case "--workspace":
        parsed.workspace = value();
        break;
      case "--resume":
      case "--continue":
        parsed.resume = true;
        break;
      case "--mode":
        parsed.mode = value();
        break;
      case "--model":
        parsed.model = value();
        break;
      case "--effort":
        parsed.effort = value();
        break;
      case "--api":
        parsed.api = value();
        break;
      case "--base-url":
        parsed.baseUrl = value();
        break;
      case "--storage-root":
        parsed.storageRoot = value();
        break;
      case "--debug-log":
        parsed.debugLog = value();
        break;
      case "--python":
        parsed.python = value();
        break;
      case "--check":
        parsed.check = true;
        break;
      case "--no-color":
        parsed.noColor = true;
        break;
      case "-h":
      case "--help":
        parsed.help = true;
        break;
      case "-v":
      case "--version":
        parsed.version = true;
        break;
      default:
        if (arg.startsWith("-")) {
          throw new Error(`Unknown option: ${arg}.`);
        }
        throw new Error(
          `Unexpected argument: ${arg}. Use --help for usage.`,
        );
    }
  }
  return parsed;
}

function parseChoice<T extends string>(
  value: string | undefined,
  allowed: readonly T[],
  name: string,
): T | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!allowed.includes(value as T)) {
    throw new Error(
      `Invalid ${name}: ${value}. Use one of: ${allowed.join(", ")}.`,
    );
  }
  return value as T;
}

function readVersion(): string {
  return packageVersion();
}

function makeRedactor(secret: string): (text: string) => string {
  if (!secret) {
    return (text) => text;
  }
  return (text) => text.split(secret).join("<redacted>");
}

function buildOptions(
  parsed: ParsedArgs,
  config: ReturnType<typeof loadConfig>,
): CliOptions {
  const mode =
    parseChoice<ApprovalMode>(
      parsed.mode,
      ["manual", "accept-write", "auto", "plan"],
      "mode",
    ) ?? "manual";
  const effort =
    parseChoice<Effort>(
      parsed.effort ?? config.effort,
      ["low", "medium", "high", "xhigh", "max"],
      "effort",
    ) ?? "high";
  const api = parseChoice<ApiProtocol>(
    parsed.api ?? config.api,
    ["openai-completions", "anthropic-messages"],
    "api protocol",
  );
  const workspace = path.resolve(parsed.workspace ?? process.cwd());
  if (!fs.existsSync(workspace)) {
    throw new Error(`Workspace does not exist: ${workspace}`);
  }
  if (!fs.statSync(workspace).isDirectory()) {
    throw new Error(`Workspace is not a directory: ${workspace}`);
  }
  return {
    workspace,
    mode,
    model: parsed.model,
    effort,
    api,
    baseUrl: parsed.baseUrl,
    storageRoot: parsed.storageRoot,
    debugLog: parsed.debugLog,
    python: parsed.python,
    resume: parsed.resume,
    check: parsed.check,
    noColor: parsed.noColor,
    help: parsed.help,
    version: parsed.version,
  };
}

async function runCheck(
  config: ReturnType<typeof loadConfig>,
  options: CliOptions,
): Promise<void> {
  const apiKey = requireApiKey(config);
  const launch = resolveSidecar(options);
  const env = buildSidecarEnv(config, options);
  const client = new SidecarClient({
    launch,
    env,
    debugLog: options.debugLog,
    redact: makeRedactor(apiKey),
  });
  try {
    await client.start();
    const initialized = await client.call<InitializeResult>(
      "initialize",
      {
        protocolVersion: 1,
        clientInfo: { name: "ling-cli", version: readVersion() },
      },
      20_000,
    );
    if (initialized.protocolVersion !== 1) {
      throw new Error(
        `Unsupported sidecar protocol: ${initialized.protocolVersion}`,
      );
    }
    const ping = await client.call(
      "ping",
      { nonce: "ling-cli-check" },
      10_000,
    );
    const snapshot = await client.call<SessionSnapshot>(
      "session.snapshot",
      {},
      20_000,
    );
    const summary = {
      ok: true,
      sidecar: {
        kind: launch.kind,
        command: launch.command,
        args: launch.args,
        cwd: launch.cwd,
      },
      config: {
        path: defaultConfigPath(),
        model: options.model ?? config.model ?? null,
        api: options.api ?? config.api ?? null,
        baseUrl: options.baseUrl ?? config.baseUrl ?? null,
      },
      protocolVersion: initialized.protocolVersion,
      serverInfo: initialized.serverInfo,
      capabilities: initialized.capabilities,
      ping,
      snapshot: {
        phase: snapshot.phase,
        sessionId: snapshot.sessionId ?? null,
        entries: snapshot.entries.length,
        pendingApprovals: snapshot.pendingApprovals.length,
        pendingQuestions: snapshot.pendingQuestions.length,
        approvalMode: snapshot.approvalMode ?? null,
        readOnly: snapshot.readOnly ?? false,
      },
    };
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const tail = client.stderrTail();
    throw new Error(tail ? `${message}\nsidecar stderr:\n${tail}` : message);
  } finally {
    await client.close();
  }
}

async function main(): Promise<void> {
  let parsed: ParsedArgs;
  try {
    parsed = parseArgs(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(
      `ling: ${error instanceof Error ? error.message : String(error)}\n`,
    );
    process.exitCode = 2;
    return;
  }

  if (parsed.help) {
    process.stdout.write(`${USAGE}\n`);
    return;
  }
  if (parsed.version) {
    process.stdout.write(`${readVersion()}\n`);
    return;
  }
  if (parsed.noColor) {
    process.env.NO_COLOR = "1";
  }

  let config: ReturnType<typeof loadConfig>;
  let options: CliOptions;
  try {
    config = loadConfigIfExists();
    options = buildOptions(parsed, config);
  } catch (error) {
    process.stderr.write(
      `ling: ${error instanceof Error ? error.message : String(error)}\n`,
    );
    process.exitCode = 1;
    return;
  }

  try {
    if (options.check) {
      await runCheck(config, options);
    } else {
      const { runTui } = await import("./tui/index.js");
      await runTui(config, options);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const redacted = makeRedactor(String(config.apiKey ?? ""))(message);
    process.stderr.write(`ling: ${redacted}\n`);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  process.stderr.write(
    `ling: ${error instanceof Error ? error.message : String(error)}\n`,
  );
  process.exitCode = 1;
});

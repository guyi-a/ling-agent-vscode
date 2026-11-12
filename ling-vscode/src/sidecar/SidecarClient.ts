import {
  spawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createInterface, type Interface } from "node:readline";

const DEFAULT_REQUEST_TIMEOUT_MS = 5_000;
const SHUTDOWN_TIMEOUT_MS = 800;
const TERMINATE_TIMEOUT_MS = 600;

export interface SidecarInitializeResult {
  protocolVersion: number;
  serverInfo: {
    name: string;
    version: string;
  };
  capabilities: {
    ping: boolean;
    sessions: boolean;
  };
}

export interface SidecarExitInfo {
  code: number | null;
  signal: NodeJS.Signals | null;
}

export interface SidecarSettings {
  apiKey?: string;
  model?: string;
  baseUrl?: string;
  maxSteps?: number;
  maxParallelTools?: number;
  commandTimeoutSeconds?: number;
  approvalTimeoutSeconds?: number;
  contextWindowTokens?: number;
  keepLastUserTurns?: number;
  browserBridgePort?: number;
  embeddingModel?: string;
  embeddingBaseUrl?: string;
  embeddingApiKey?: string;
}

export interface SidecarClientOptions extends SidecarSettings {
  extensionPath: string;
  developmentMode?: boolean;
  output?: (line: string) => void;
  onExit?: (info: SidecarExitInfo) => void;
  requestTimeoutMs?: number;
}

interface SidecarLaunch {
  command: string;
  args: string[];
  cwd: string;
  mode: "python" | "binary";
}

interface PendingRequest {
  method: string;
  resolve(value: unknown): void;
  reject(error: Error): void;
  timer: NodeJS.Timeout;
}

interface JsonRpcSuccess {
  jsonrpc: "2.0";
  id: string | number | null;
  result: unknown;
}

interface JsonRpcFailure {
  jsonrpc: "2.0";
  id: string | number | null;
  error: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export function resolveSkillsRoot(
  extensionPath: string,
): string | undefined {
  const override = process.env.LING_SKILLS_ROOT?.trim();
  if (override && existsSync(override)) {
    return override;
  }

  const bundled = join(extensionPath, "resources", "skills");
  if (existsSync(bundled)) {
    return bundled;
  }

  const dev = resolve(extensionPath, "..", ".agents", "skills");
  return existsSync(dev) ? dev : undefined;
}

export function buildSidecarEnv(
  settings: SidecarSettings,
  base: NodeJS.ProcessEnv = process.env,
  skillsRoot?: string,
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...base };
  delete env.LING_API_KEY;
  delete env.DEEPSEEK_API_KEY;
  delete env.OPENAI_API_KEY;
  delete env.LING_MODEL;
  delete env.LING_API_BASE_URL;
  delete env.LING_PROVIDER;
  delete env.LING_SKILLS_ROOT;
  delete env.LING_MAX_STEPS;
  delete env.LING_MAX_PARALLEL_TOOLS;
  delete env.LING_COMMAND_TIMEOUT_SECONDS;
  delete env.LING_APPROVAL_TIMEOUT_SECONDS;
  delete env.LING_CONTEXT_WINDOW_TOKENS;
  delete env.LING_KEEP_LAST_USER_TURNS;
  delete env.LING_BROWSER_BRIDGE_PORT;
  delete env.LING_EMBEDDING_MODEL;
  delete env.LING_EMBEDDING_BASE_URL;
  delete env.LING_EMBEDDING_API_KEY;
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUNBUFFERED = "1";
  env.PYTHONUTF8 = "1";
  if (settings.apiKey) {
    env.LING_API_KEY = settings.apiKey;
  }
  if (settings.model) {
    env.LING_MODEL = settings.model;
  }
  if (settings.baseUrl) {
    env.LING_API_BASE_URL = settings.baseUrl;
  }
  if (skillsRoot) {
    env.LING_SKILLS_ROOT = skillsRoot;
  }
  if (settings.embeddingModel) {
    env.LING_EMBEDDING_MODEL = settings.embeddingModel;
  }
  if (settings.embeddingBaseUrl) {
    env.LING_EMBEDDING_BASE_URL = settings.embeddingBaseUrl;
  }
  if (settings.embeddingApiKey) {
    env.LING_EMBEDDING_API_KEY = settings.embeddingApiKey;
  }
  setNumberEnv(env, "LING_MAX_STEPS", settings.maxSteps);
  setNumberEnv(
    env,
    "LING_MAX_PARALLEL_TOOLS",
    settings.maxParallelTools,
  );
  setNumberEnv(
    env,
    "LING_COMMAND_TIMEOUT_SECONDS",
    settings.commandTimeoutSeconds,
  );
  setNumberEnv(
    env,
    "LING_APPROVAL_TIMEOUT_SECONDS",
    settings.approvalTimeoutSeconds,
  );
  setNumberEnv(
    env,
    "LING_CONTEXT_WINDOW_TOKENS",
    settings.contextWindowTokens,
  );
  setNumberEnv(
    env,
    "LING_KEEP_LAST_USER_TURNS",
    settings.keepLastUserTurns,
  );
  setNumberEnv(
    env,
    "LING_BROWSER_BRIDGE_PORT",
    settings.browserBridgePort,
  );
  return env;
}

function setNumberEnv(
  env: NodeJS.ProcessEnv,
  name: string,
  value: number | undefined,
): void {
  if (value !== undefined && Number.isFinite(value)) {
    env[name] = String(value);
  }
}

export class SidecarRpcError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly data?: unknown,
  ) {
    super(message);
    this.name = "SidecarRpcError";
  }
}

export class SidecarClient {
  onExit: ((info: SidecarExitInfo) => void) | undefined;
  onNotification:
    | ((method: string, params: unknown) => void)
    | undefined;

  private child: ChildProcessWithoutNullStreams | undefined;
  private outputLines: Interface | undefined;
  private nextRequestId = 1;
  private readonly pending = new Map<number, PendingRequest>();
  private initialized: SidecarInitializeResult | undefined;
  private startPromise: Promise<SidecarInitializeResult> | undefined;
  private expectedExit = false;
  private settings: SidecarSettings;

  constructor(private readonly options: SidecarClientOptions) {
    this.onExit = options.onExit;
    this.settings = {
      apiKey: options.apiKey,
      model: options.model,
      baseUrl: options.baseUrl,
      maxSteps: options.maxSteps,
      maxParallelTools: options.maxParallelTools,
      commandTimeoutSeconds: options.commandTimeoutSeconds,
      approvalTimeoutSeconds: options.approvalTimeoutSeconds,
      contextWindowTokens: options.contextWindowTokens,
      keepLastUserTurns: options.keepLastUserTurns,
      browserBridgePort: options.browserBridgePort,
      embeddingModel: options.embeddingModel,
      embeddingBaseUrl: options.embeddingBaseUrl,
      embeddingApiKey: options.embeddingApiKey,
    };
  }

  setSettings(settings: SidecarSettings): void {
    this.settings = { ...settings };
  }

  get isRunning(): boolean {
    return this.child !== undefined && this.child.exitCode === null;
  }

  get isInitialized(): boolean {
    return this.initialized !== undefined;
  }

  get pid(): number | undefined {
    return this.child?.pid;
  }

  async start(): Promise<SidecarInitializeResult> {
    if (this.initialized && this.isRunning) {
      return this.initialized;
    }

    this.startPromise ??= this.startInternal().finally(() => {
      this.startPromise = undefined;
    });
    return this.startPromise;
  }

  async restart(): Promise<SidecarInitializeResult> {
    await this.stop();
    return this.start();
  }

  async ping(nonce = ""): Promise<void> {
    await this.start();
    const result = await this.request("ping", { nonce });
    if (!isRecord(result) || result.pong !== true) {
      throw new Error("Sidecar returned an invalid ping response");
    }
  }

  call<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = this.options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    return this.request(method, params, timeoutMs) as Promise<T>;
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) {
      this.resetState();
      return;
    }

    this.expectedExit = true;
    try {
      await this.request("shutdown", {}, SHUTDOWN_TIMEOUT_MS);
    } catch {
      // A broken sidecar still needs to be terminated.
    }

    if (child.stdin.writable) {
      child.stdin.end();
    }

    const exited = await waitForExit(child, TERMINATE_TIMEOUT_MS);
    if (!exited && child.exitCode === null && child.signalCode === null) {
      child.kill();
      await waitForExit(child, TERMINATE_TIMEOUT_MS);
    }

    this.resetState();
  }

  dispose(): void {
    void this.stop();
  }

  private async startInternal(): Promise<SidecarInitializeResult> {
    if (
      this.child &&
      this.child.exitCode === null &&
      this.child.signalCode === null
    ) {
      throw new Error("Sidecar process is already running");
    }

    this.expectedExit = false;
    const launch = this.resolveLaunch();
    this.log(
      this.settings.apiKey
        ? "Sidecar API key source: config file"
        : "Sidecar API key source: none",
    );
    this.log(`Starting sidecar (${launch.mode}): ${launch.command}`);

    const env = buildSidecarEnv(
      this.settings,
      process.env,
      resolveSkillsRoot(this.options.extensionPath),
    );

    const child = spawn(launch.command, launch.args, {
      cwd: launch.cwd,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env,
    });
    this.child = child;
    this.attachProcess(child);

    try {
      const result = await this.request(
        "initialize",
        {
          protocolVersion: 1,
          clientInfo: {
            name: "ling-vscode",
            version: "0.10.0",
          },
        },
        this.options.requestTimeoutMs,
      );

      if (!isInitializeResult(result) || result.protocolVersion !== 1) {
        throw new Error("Sidecar returned an invalid initialize response");
      }

      this.initialized = result;
      this.log(
        `Sidecar ready: ${result.serverInfo.name} ${result.serverInfo.version}`,
      );
      return result;
    } catch (error) {
      this.expectedExit = true;
      child.kill();
      this.child = undefined;
      this.initialized = undefined;
      this.rejectPending(new Error("Ling sidecar failed during startup"));
      throw error;
    }
  }

  private attachProcess(child: ChildProcessWithoutNullStreams): void {
    this.outputLines = createInterface({
      input: child.stdout,
      crlfDelay: Number.POSITIVE_INFINITY,
    });

    this.outputLines.on("line", (line) => {
      this.handleStdoutLine(line);
    });

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      const lines = chunk.split(/\r?\n/).filter(Boolean);
      lines.forEach((line) => this.log(`[sidecar] ${line}`));
    });

    child.stdin.on("error", (error) => {
      this.log(`Sidecar stdin error: ${error.message}`);
    });

    child.on("error", (error) => {
      this.log(`Sidecar process error: ${error.message}`);
      this.rejectPending(
        new Error(`Unable to start Ling sidecar: ${error.message}`),
      );
    });

    child.on("exit", (code, signal) => {
      const wasExpected = this.expectedExit;
      this.outputLines?.close();
      this.outputLines = undefined;
      this.child = undefined;
      this.initialized = undefined;
      this.rejectPending(
        new Error(
          `Ling sidecar exited (${signal ?? code ?? "unknown"})`,
        ),
      );
      this.log(
        `Sidecar exited: code=${code ?? "null"} signal=${signal ?? "null"}`,
      );

      if (!wasExpected) {
        this.onExit?.({ code, signal });
      }
    });
  }

  private handleStdoutLine(line: string): void {
    if (!line.trim()) {
      return;
    }

    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      this.log(`Invalid sidecar JSON: ${line}`);
      return;
    }

    if (!isRecord(message) || message.jsonrpc !== "2.0") {
      this.log(`Unexpected sidecar message: ${line}`);
      return;
    }

    if (!("id" in message)) {
      if (typeof message.method === "string") {
        this.onNotification?.(message.method, message.params);
      } else {
        this.log(`Invalid sidecar notification: ${line}`);
      }
      return;
    }

    const requestId = message.id;
    if (typeof requestId !== "number") {
      this.log(`Unsupported sidecar response id: ${String(requestId)}`);
      return;
    }

    const pending = this.pending.get(requestId);
    if (!pending) {
      this.log(`Unmatched sidecar response id: ${requestId}`);
      return;
    }

    this.pending.delete(requestId);
    clearTimeout(pending.timer);

    if ("error" in message) {
      const failure = message as unknown as JsonRpcFailure;
      pending.reject(
        new SidecarRpcError(
          failure.error.code,
          failure.error.message,
          failure.error.data,
        ),
      );
      return;
    }

    pending.resolve((message as unknown as JsonRpcSuccess).result);
  }

  private request(
    method: string,
    params: Record<string, unknown>,
    timeoutMs = this.options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<unknown> {
    const child = this.child;
    if (
      !child ||
      child.exitCode !== null ||
      child.signalCode !== null ||
      !child.stdin.writable
    ) {
      return Promise.reject(
        new Error(`Cannot send ${method}: sidecar is not running`),
      );
    }

    const id = this.nextRequestId++;
    const payload = JSON.stringify({
      jsonrpc: "2.0",
      id,
      method,
      params,
    });

    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(
          new Error(`Sidecar request timed out: ${method}`),
        );
      }, timeoutMs);

      this.pending.set(id, {
        method,
        resolve: resolvePromise,
        reject: rejectPromise,
        timer,
      });

      child.stdin.write(`${payload}\n`, "utf8", (error) => {
        if (!error) {
          return;
        }

        const pending = this.pending.get(id);
        if (pending) {
          this.pending.delete(id);
          clearTimeout(pending.timer);
          pending.reject(
            new Error(`Failed to send ${method}: ${error.message}`),
          );
        }
      });
    });
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private resetState(): void {
    this.outputLines?.close();
    this.outputLines = undefined;
    this.child = undefined;
    this.initialized = undefined;
    this.expectedExit = false;
    this.rejectPending(new Error("Ling sidecar stopped"));
  }

  private resolveLaunch(): SidecarLaunch {
    const explicitSidecar = process.env.LING_SIDECAR_PATH?.trim();
    if (explicitSidecar) {
      return {
        command: explicitSidecar,
        args: [],
        cwd: dirname(explicitSidecar),
        mode: "binary",
      };
    }

    const pythonRoot = resolve(this.options.extensionPath, "..", "ling");
    const python =
      process.env.LING_PYTHON_PATH?.trim() ||
      (process.platform === "win32" ? "python" : "python3");
    const sourceLaunch: SidecarLaunch = {
      command: python,
      args: ["-m", "ling_rpc"],
      cwd: pythonRoot,
      mode: "python",
    };
    if (
      this.options.developmentMode &&
      existsSync(join(pythonRoot, "ling_rpc"))
    ) {
      return sourceLaunch;
    }

    const bundledNames =
      process.platform === "win32"
        ? ["ling-sidecar.exe", "ling-sidecar"]
        : ["ling-sidecar", "ling-sidecar.exe"];
    for (const name of bundledNames) {
      const candidate = join(
        this.options.extensionPath,
        "resources",
        "sidecar",
        name,
      );
      if (existsSync(candidate)) {
        return {
          command: candidate,
          args: [],
          cwd: dirname(candidate),
          mode: "binary",
        };
      }
    }

    if (existsSync(join(pythonRoot, "ling_rpc"))) {
      return sourceLaunch;
    }
    throw new Error(
      `Ling sidecar was not found. Expected ${pythonRoot} or resources/sidecar.`,
    );
  }

  private log(line: string): void {
    this.options.output?.(line);
  }
}

function isInitializeResult(value: unknown): value is SidecarInitializeResult {
  return (
    isRecord(value) &&
    typeof value.protocolVersion === "number" &&
    isRecord(value.serverInfo) &&
    typeof value.serverInfo.name === "string" &&
    typeof value.serverInfo.version === "string" &&
    isRecord(value.capabilities) &&
    typeof value.capabilities.ping === "boolean" &&
    typeof value.capabilities.sessions === "boolean"
  );
}

function waitForExit(
  child: ChildProcessWithoutNullStreams,
  timeoutMs: number,
): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true);
  }

  return new Promise((resolvePromise) => {
    const timer = setTimeout(() => {
      child.removeListener("exit", handleExit);
      resolvePromise(false);
    }, timeoutMs);

    function handleExit(): void {
      clearTimeout(timer);
      resolvePromise(true);
    }

    child.once("exit", handleExit);
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

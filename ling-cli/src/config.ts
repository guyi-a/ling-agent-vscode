import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const packageRoot = path.resolve(
  fileURLToPath(new URL("..", import.meta.url)),
);
export const repoRoot = path.resolve(packageRoot, "..");

export interface LingConfig {
  version?: number;
  apiKey?: string;
  model?: string;
  baseUrl?: string;
  api?: string;
  effort?: string;
  maxSteps?: number;
  maxParallelTools?: number;
  commandTimeoutSeconds?: number;
  approvalTimeoutSeconds?: number;
  contextWindowTokens?: number;
  keepLastUserTurns?: number;
  embeddingModel?: string;
  embeddingBaseUrl?: string;
  embeddingApiKey?: string;
  computerUseEnabled?: boolean;
  computerSessionTtlSeconds?: number;
  computerSessionIdleSeconds?: number;
  computerDeniedApps?: string;
  computerAuditEnabled?: boolean;
  browserBridgePort?: number;
  [key: string]: unknown;
}

export type ApprovalMode = "manual" | "accept-write" | "auto" | "plan";
export type Effort = "low" | "medium" | "high" | "xhigh" | "max";
export type ApiProtocol = "openai-completions" | "anthropic-messages";

export interface CliOptions {
  workspace: string;
  mode: ApprovalMode;
  model?: string;
  effort: Effort;
  api?: ApiProtocol;
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

export interface SidecarLaunch {
  kind: "exe" | "python";
  command: string;
  args: string[];
  cwd: string;
}

const CLEARED_ENV = [
  "LING_API_KEY",
  "DEEPSEEK_API_KEY",
  "OPENAI_API_KEY",
  "LING_MODEL",
  "LING_API_BASE_URL",
  "LING_API_PROTOCOL",
  "LING_PROVIDER",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_BASE_URL",
  "ANTHROPIC_MODEL",
  "LING_SKILLS_ROOT",
  "LING_MAX_STEPS",
  "LING_MAX_PARALLEL_TOOLS",
  "LING_COMMAND_TIMEOUT_SECONDS",
  "LING_APPROVAL_TIMEOUT_SECONDS",
  "LING_CONTEXT_WINDOW_TOKENS",
  "LING_KEEP_LAST_USER_TURNS",
  "LING_BROWSER_BRIDGE_PORT",
  "LING_EMBEDDING_MODEL",
  "LING_EMBEDDING_BASE_URL",
  "LING_EMBEDDING_API_KEY",
  "LING_COMPUTER_ENABLED",
  "LING_COMPUTER_SESSION_TTL_SECONDS",
  "LING_COMPUTER_SESSION_IDLE_SECONDS",
  "LING_COMPUTER_DENIED_APPS",
  "LING_COMPUTER_AUDIT_ENABLED",
  "LING_STORAGE_ROOT",
];

export function defaultConfigPath(): string {
  if (process.platform === "win32") {
    const appData = process.env.APPDATA;
    return appData
      ? path.join(appData, "Ling", "config.json")
      : path.join(
          os.homedir(),
          "AppData",
          "Roaming",
          "Ling",
          "config.json",
        );
  }
  const configHome = process.env.XDG_CONFIG_HOME;
  return configHome
    ? path.join(configHome, "ling-agent", "config.json")
    : path.join(os.homedir(), ".config", "ling-agent", "config.json");
}

export function loadConfig(configPath = defaultConfigPath()): LingConfig {
  let raw: string;
  try {
    raw = fs.readFileSync(configPath, "utf8");
  } catch {
    throw new Error(
      `Ling config not found at ${configPath}. Configure Ling in VS Code first.`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`Ling config is not valid JSON: ${configPath}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`Ling config must be a JSON object: ${configPath}`);
  }
  return parsed as LingConfig;
}

export function requireApiKey(config: LingConfig): string {
  const key = typeof config.apiKey === "string" ? config.apiKey.trim() : "";
  if (!key) {
    throw new Error(
      `Ling has no API key. Set it in VS Code or in ${defaultConfigPath()}.`,
    );
  }
  return key;
}

function setIf(
  env: NodeJS.ProcessEnv,
  name: string,
  value: string | number | undefined | null,
): void {
  if (value === undefined || value === null) {
    return;
  }
  const text = String(value).trim();
  if (text) {
    env[name] = text;
  }
}

function findSkillsRoot(): string | undefined {
  const candidates = [
    path.join(repoRoot, ".agents", "skills"),
    path.join(packageRoot, "skills"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

export function buildSidecarEnv(
  config: LingConfig,
  options: CliOptions,
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const name of CLEARED_ENV) {
    delete env[name];
  }

  setIf(env, "LING_API_KEY", requireApiKey(config));
  setIf(env, "LING_MODEL", options.model ?? config.model);
  setIf(env, "LING_API_BASE_URL", options.baseUrl ?? config.baseUrl);
  setIf(env, "LING_API_PROTOCOL", options.api ?? config.api);
  setIf(env, "LING_MAX_STEPS", config.maxSteps);
  setIf(env, "LING_MAX_PARALLEL_TOOLS", config.maxParallelTools);
  setIf(env, "LING_COMMAND_TIMEOUT_SECONDS", config.commandTimeoutSeconds);
  setIf(env, "LING_APPROVAL_TIMEOUT_SECONDS", config.approvalTimeoutSeconds);
  setIf(env, "LING_CONTEXT_WINDOW_TOKENS", config.contextWindowTokens);
  setIf(env, "LING_KEEP_LAST_USER_TURNS", config.keepLastUserTurns);
  setIf(env, "LING_BROWSER_BRIDGE_PORT", config.browserBridgePort);
  setIf(env, "LING_EMBEDDING_MODEL", config.embeddingModel);
  setIf(env, "LING_EMBEDDING_BASE_URL", config.embeddingBaseUrl);
  setIf(env, "LING_EMBEDDING_API_KEY", config.embeddingApiKey);
  if (config.computerUseEnabled !== undefined) {
    env.LING_COMPUTER_ENABLED = config.computerUseEnabled ? "1" : "0";
  }
  setIf(
    env,
    "LING_COMPUTER_SESSION_TTL_SECONDS",
    config.computerSessionTtlSeconds,
  );
  setIf(
    env,
    "LING_COMPUTER_SESSION_IDLE_SECONDS",
    config.computerSessionIdleSeconds,
  );
  setIf(env, "LING_COMPUTER_DENIED_APPS", config.computerDeniedApps);
  if (config.computerAuditEnabled !== undefined) {
    env.LING_COMPUTER_AUDIT_ENABLED = config.computerAuditEnabled
      ? "1"
      : "0";
  }
  if (options.storageRoot) {
    env.LING_STORAGE_ROOT = path.resolve(options.storageRoot);
  }
  const skillsRoot = findSkillsRoot();
  if (skillsRoot) {
    env.LING_SKILLS_ROOT = skillsRoot;
  }
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUTF8 = "1";
  env.PYTHONUNBUFFERED = "1";
  return env;
}

function exists(filePath: string): boolean {
  try {
    fs.accessSync(filePath);
    return true;
  } catch {
    return false;
  }
}

export function resolveSidecar(options: CliOptions): SidecarLaunch {
  const explicit = process.env.LING_SIDECAR_PATH?.trim();
  if (explicit) {
    return {
      kind: "exe",
      command: explicit,
      args: [],
      cwd: path.dirname(explicit),
    };
  }

  const exeName =
    process.platform === "win32" ? "ling-sidecar.exe" : "ling-sidecar";
  const vendor = path.join(
    packageRoot,
    "vendor",
    `${process.platform}-${process.arch}`,
    exeName,
  );
  if (exists(vendor)) {
    return {
      kind: "exe",
      command: vendor,
      args: [],
      cwd: path.dirname(vendor),
    };
  }

  const candidates = [
    path.join(repoRoot, "ling"),
    path.join(process.cwd(), "ling"),
  ];
  const lingRoot = candidates.find((candidate) =>
    exists(path.join(candidate, "ling_rpc", "__main__.py")),
  );
  if (!lingRoot) {
    throw new Error(
      "Cannot find the Ling sidecar. Set LING_SIDECAR_PATH to a built " +
        "ling-sidecar executable, or run from a Ling source checkout.",
    );
  }
  const python =
    options.python ?? process.env.LING_PYTHON_PATH?.trim() ?? "python";
  return {
    kind: "python",
    command: python,
    args: ["-m", "ling_rpc"],
    cwd: lingRoot,
  };
}

import {
  chmod,
  mkdir,
  readFile,
  rename,
  unlink,
  writeFile,
} from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export interface LingSettings {
  version: 1;
  apiKey: string;
  model: string;
  baseUrl: string;
}

export interface MigrationResult {
  settings: LingSettings;
  migrated: boolean;
  source?: "LING_API_KEY" | "DEEPSEEK_API_KEY" | "OPENAI_API_KEY";
}

export const DEFAULT_SETTINGS: LingSettings = {
  version: 1,
  apiKey: "",
  model: "deepseek-chat",
  baseUrl: "https://api.deepseek.com/v1",
};

const KEY_ENV_VARS = [
  "LING_API_KEY",
  "DEEPSEEK_API_KEY",
  "OPENAI_API_KEY",
] as const;

export function settingsPath(): string {
  if (process.platform === "win32") {
    const base =
      process.env.APPDATA ?? join(homedir(), "AppData", "Roaming");
    return join(base, "Ling", "config.json");
  }
  if (process.platform === "darwin") {
    return join(
      homedir(),
      "Library",
      "Application Support",
      "Ling",
      "config.json",
    );
  }
  const base = process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config");
  return join(base, "ling", "config.json");
}

export async function readSettings(
  filePath = settingsPath(),
): Promise<LingSettings> {
  let raw: string;
  try {
    raw = await readFile(filePath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { ...DEFAULT_SETTINGS };
    }
    throw error;
  }

  try {
    return normalizeSettings(JSON.parse(raw));
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Invalid Ling settings at ${filePath}: ${detail}`);
  }
}

export async function writeSettings(
  settings: LingSettings,
  filePath = settingsPath(),
): Promise<void> {
  const normalized = normalizeSettings(settings);
  const data = `${JSON.stringify(normalized, null, 2)}\n`;
  await mkdir(dirname(filePath), { recursive: true });

  const tempPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(tempPath, data, { encoding: "utf8", mode: 0o600 });

  try {
    await rename(tempPath, filePath);
  } catch {
    await writeFile(filePath, data, { encoding: "utf8", mode: 0o600 });
    try {
      await unlink(tempPath);
    } catch {
      // Best-effort cleanup of the temporary file.
    }
  }

  try {
    await chmod(filePath, 0o600);
  } catch {
    // Windows does not map POSIX modes directly.
  }
}

export function migrateApiKey(
  settings: LingSettings,
  env: NodeJS.ProcessEnv = process.env,
): MigrationResult {
  if (settings.apiKey) {
    return { settings, migrated: false };
  }

  for (const source of KEY_ENV_VARS) {
    const value = env[source]?.trim();
    if (value) {
      return {
        settings: { ...settings, apiKey: value },
        migrated: true,
        source,
      };
    }
  }

  return { settings, migrated: false };
}

function normalizeSettings(value: unknown): LingSettings {
  const record = isRecord(value) ? value : {};
  const apiKey =
    typeof record.apiKey === "string" ? record.apiKey.trim() : "";
  const model =
    typeof record.model === "string" && record.model.trim()
      ? record.model.trim()
      : DEFAULT_SETTINGS.model;
  const baseUrlInput =
    typeof record.baseUrl === "string" && record.baseUrl.trim()
      ? record.baseUrl.trim()
      : DEFAULT_SETTINGS.baseUrl;
  const baseUrl = baseUrlInput.replace(/\/+$/, "");

  return {
    version: 1,
    apiKey,
    model,
    baseUrl: baseUrl || DEFAULT_SETTINGS.baseUrl,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

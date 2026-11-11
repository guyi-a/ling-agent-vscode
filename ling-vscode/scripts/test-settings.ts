import assert from "node:assert/strict";
import {
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  DEFAULT_SETTINGS,
  migrateApiKey,
  readSettings,
  writeSettings,
} from "../src/settings/config";

async function main(): Promise<void> {
  const dir = await mkdtemp(join(tmpdir(), "ling-settings-"));
  const configPath = join(dir, "nested", "config.json");

  try {
    const missing = await readSettings(configPath);
    assert.deepEqual(missing, DEFAULT_SETTINGS);
    assert.equal(missing.effort, "high");
    assert.equal(missing.maxSteps, 10);

    const rawPath = join(dir, "raw.json");
    await writeFile(
      rawPath,
      JSON.stringify({
        version: 1,
        apiKey: "",
        model: "deepseek-chat",
        baseUrl: "https://api.deepseek.com/v1",
        effort: "turbo",
        maxSteps: 0,
        maxParallelTools: 99,
        commandTimeoutSeconds: -1,
        approvalTimeoutSeconds: 45,
        contextWindowTokens: 2048,
        keepLastUserTurns: 51,
      }),
      "utf8",
    );
    const normalized = await readSettings(rawPath);
    assert.equal(normalized.effort, "high");
    assert.equal(normalized.maxSteps, 10);
    assert.equal(normalized.maxParallelTools, 4);
    assert.equal(normalized.commandTimeoutSeconds, 120);
    assert.equal(normalized.approvalTimeoutSeconds, 45);
    assert.equal(normalized.contextWindowTokens, 2048);
    assert.equal(normalized.keepLastUserTurns, 5);

    const migrated = migrateApiKey(
      { ...DEFAULT_SETTINGS, apiKey: "" },
      { DEEPSEEK_API_KEY: "sk-deepseek" },
    );
    assert.equal(migrated.migrated, true);
    assert.equal(migrated.source, "DEEPSEEK_API_KEY");
    assert.equal(migrated.settings.apiKey, "sk-deepseek");

    await writeSettings(
      {
        ...migrated.settings,
        model: " deepseek-reasoner ",
        baseUrl: "https://example.test/v1///",
        effort: "xhigh",
      },
      configPath,
    );

    const loaded = await readSettings(configPath);
    assert.equal(loaded.model, "deepseek-reasoner");
    assert.equal(loaded.baseUrl, "https://example.test/v1");
    assert.equal(loaded.apiKey, "sk-deepseek");
    assert.equal(loaded.effort, "xhigh");

    const raw = JSON.parse(await readFile(configPath, "utf8")) as {
      version: number;
    };
    assert.equal(raw.version, 1);

    const existing = migrateApiKey(
      { ...DEFAULT_SETTINGS, apiKey: "sk-existing" },
      { LING_API_KEY: "sk-ling", DEEPSEEK_API_KEY: "sk-deepseek" },
    );
    assert.equal(existing.migrated, false);
    assert.equal(existing.settings.apiKey, "sk-existing");

    const precedence = migrateApiKey(
      { ...DEFAULT_SETTINGS, apiKey: "" },
      { LING_API_KEY: "sk-ling", DEEPSEEK_API_KEY: "sk-deepseek" },
    );
    assert.equal(precedence.settings.apiKey, "sk-ling");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }

  console.log("Ling settings tests passed");
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});

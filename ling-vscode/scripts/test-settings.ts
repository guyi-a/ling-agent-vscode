import assert from "node:assert/strict";
import {
  mkdtemp,
  readFile,
  rm,
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
      },
      configPath,
    );

    const loaded = await readSettings(configPath);
    assert.equal(loaded.model, "deepseek-reasoner");
    assert.equal(loaded.baseUrl, "https://example.test/v1");
    assert.equal(loaded.apiKey, "sk-deepseek");

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

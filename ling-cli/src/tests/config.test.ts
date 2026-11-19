import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildSidecarEnv,
  loadConfig,
  requireApiKey,
  resolveSidecar,
  type CliOptions,
} from "../config.js";

function options(overrides: Partial<CliOptions> = {}): CliOptions {
  return {
    workspace: path.resolve("."),
    mode: "manual",
    effort: "low",
    resume: false,
    check: false,
    noColor: false,
    help: false,
    version: false,
    ...overrides,
  };
}

function tempDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ling-cli-test-"));
}

test("loads config and validates the API key", () => {
  const root = tempDir();
  const configPath = path.join(root, "config.json");
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      version: 1,
      apiKey: "  test-key  ",
      model: "cfg-model",
      baseUrl: "https://example.test/v1",
      api: "openai-completions",
    }),
    "utf8",
  );
  try {
    const config = loadConfig(configPath);
    assert.equal(config.model, "cfg-model");
    assert.equal(requireApiKey(config), "test-key");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("rejects missing, invalid, and keyless configs", () => {
  const root = tempDir();
  try {
    assert.throws(
      () => loadConfig(path.join(root, "missing.json")),
      /not found/,
    );
    const invalid = path.join(root, "invalid.json");
    fs.writeFileSync(invalid, "{not json", "utf8");
    assert.throws(() => loadConfig(invalid), /not valid JSON/);
    const keyless = path.join(root, "keyless.json");
    fs.writeFileSync(keyless, JSON.stringify({ model: "m" }), "utf8");
    assert.throws(() => requireApiKey(loadConfig(keyless)), /no API key/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("clears shell overrides and does not mutate process.env", () => {
  const saved = {
    LING_MODEL: process.env.LING_MODEL,
    LING_API_KEY: process.env.LING_API_KEY,
    LING_STORAGE_ROOT: process.env.LING_STORAGE_ROOT,
  };
  process.env.LING_MODEL = "leaked-model";
  process.env.LING_API_KEY = "leaked-key";
  process.env.LING_STORAGE_ROOT = "C:\\leaked";
  try {
    const env = buildSidecarEnv(
      {
        apiKey: "real-key",
        model: "cfg-model",
        baseUrl: "https://example.test/v1",
        api: "openai-completions",
        maxSteps: 7,
        contextWindowTokens: 200000,
      },
      options({ model: "cli-model" }),
    );
    assert.equal(env.LING_API_KEY, "real-key");
    assert.equal(env.LING_MODEL, "cli-model");
    assert.equal(env.LING_MAX_STEPS, "7");
    assert.equal(env.LING_CONTEXT_WINDOW_TOKENS, "200000");
    assert.equal(env.LING_STORAGE_ROOT, undefined);
    assert.equal(env.PYTHONUTF8, "1");
    assert.equal(process.env.LING_MODEL, "leaked-model");
    assert.equal(process.env.LING_API_KEY, "leaked-key");
  } finally {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) {
        delete process.env[name];
      } else {
        process.env[name] = value;
      }
    }
  }
});

test("sets an explicit storage root and resolves an explicit sidecar", () => {
  const root = tempDir();
  const saved = process.env.LING_SIDECAR_PATH;
  const exe = path.join(root, "ling-sidecar.exe");
  fs.writeFileSync(exe, "stub", "utf8");
  process.env.LING_SIDECAR_PATH = exe;
  try {
    const env = buildSidecarEnv(
      { apiKey: "key" },
      options({ storageRoot: path.join(root, "state") }),
    );
    assert.equal(
      env.LING_STORAGE_ROOT,
      path.resolve(root, "state"),
    );
    const launch = resolveSidecar(options());
    assert.equal(launch.kind, "exe");
    assert.equal(launch.command, exe);
    assert.equal(launch.cwd, root);
  } finally {
    if (saved === undefined) {
      delete process.env.LING_SIDECAR_PATH;
    } else {
      process.env.LING_SIDECAR_PATH = saved;
    }
    fs.rmSync(root, { recursive: true, force: true });
  }
});

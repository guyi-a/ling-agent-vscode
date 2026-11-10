import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  buildSidecarEnv,
  resolveSkillsRoot,
  SidecarClient,
} from "../src/sidecar/SidecarClient";

function testSkillsRoot(): void {
  const previous = process.env.LING_SKILLS_ROOT;
  delete process.env.LING_SKILLS_ROOT;
  const temp = mkdtempSync(join(tmpdir(), "ling-skills-"));
  try {
    const extensionPath = join(temp, "extension");
    mkdirSync(extensionPath);
    assert.equal(resolveSkillsRoot(extensionPath), undefined);

    const dev = resolve(extensionPath, "..", ".agents", "skills");
    mkdirSync(dev, { recursive: true });
    assert.equal(resolveSkillsRoot(extensionPath), dev);

    const bundled = join(extensionPath, "resources", "skills");
    mkdirSync(bundled, { recursive: true });
    assert.equal(resolveSkillsRoot(extensionPath), bundled);

    const env = buildSidecarEnv(
      {},
      { ...process.env, LING_SKILLS_ROOT: "stale" },
      bundled,
    );
    assert.equal(env.LING_SKILLS_ROOT, bundled);
  } finally {
    rmSync(temp, { recursive: true, force: true });
    if (previous !== undefined) {
      process.env.LING_SKILLS_ROOT = previous;
    }
  }
}

async function main(): Promise<void> {
  testSkillsRoot();

  const configured = buildSidecarEnv(
    {
      apiKey: "sk-config",
      model: "deepseek-chat",
      baseUrl: "https://api.deepseek.com/v1",
    },
    {
      ...process.env,
      LING_API_KEY: "sk-env",
      DEEPSEEK_API_KEY: "sk-deepseek",
      OPENAI_API_KEY: "sk-openai",
      LING_MODEL: "env-model",
      LING_API_BASE_URL: "https://env.test",
    },
  );
  assert.equal(configured.LING_API_KEY, "sk-config");
  assert.equal(configured.DEEPSEEK_API_KEY, undefined);
  assert.equal(configured.OPENAI_API_KEY, undefined);
  assert.equal(configured.LING_MODEL, "deepseek-chat");
  assert.equal(configured.LING_API_BASE_URL, "https://api.deepseek.com/v1");

  const unconfigured = buildSidecarEnv(
    {},
    {
      ...process.env,
      DEEPSEEK_API_KEY: "sk-deepseek",
    },
  );
  assert.equal(unconfigured.LING_API_KEY, undefined);
  assert.equal(unconfigured.DEEPSEEK_API_KEY, undefined);

  const lines: string[] = [];
  let resolveUnexpectedExit: () => void = () => undefined;
  const unexpectedExit = new Promise<void>((resolvePromise) => {
    resolveUnexpectedExit = resolvePromise;
  });
  const client = new SidecarClient({
    extensionPath: process.cwd(),
    requestTimeoutMs: 4_000,
    output: (line) => lines.push(line),
    onExit: () => resolveUnexpectedExit(),
  });

  try {
    const initialized = await client.start();
    assert.equal(initialized.protocolVersion, 1);
    assert.equal(initialized.serverInfo.name, "ling-sidecar");
    await client.ping("first");

    const mode = await client.call<{ mode: string }>(
      "session.setMode",
      { mode: "auto" },
    );
    assert.equal(mode.mode, "auto");
    await assert.rejects(
      client.call("session.setMode", { mode: "sometimes" }),
    );
    const snapshot = await client.call<{
      phase: string;
      approvalMode?: string;
    }>("session.snapshot");
    assert.equal(snapshot.phase, "idle");
    assert.equal(snapshot.approvalMode, "auto");

    await client.stop();
    assert.equal(client.isRunning, false);

    const restarted = await client.start();
    assert.equal(restarted.protocolVersion, 1);
    await client.ping("second");

    assert.ok(client.pid);
    process.kill(client.pid, "SIGTERM");
    await Promise.race([
      unexpectedExit,
      new Promise((_resolve, reject) => {
        setTimeout(
          () => reject(new Error("sidecar exit callback timed out")),
          3_000,
        );
      }),
    ]);
    assert.equal(client.isRunning, false);

    const recovered = await client.start();
    assert.equal(recovered.protocolVersion, 1);
    await client.ping("recovered");
    await client.stop();
  } finally {
    await client.stop();
  }

  console.log("SidecarClient integration test passed");
  console.log(lines.join("\n"));
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});

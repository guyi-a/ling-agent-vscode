import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
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
  assert.equal(configured.LING_EMBEDDING_MODEL, undefined);

  const browser = buildSidecarEnv(
    { browserBridgePort: 17323 },
    { ...process.env, LING_BROWSER_BRIDGE_PORT: "9999" },
  );
  assert.equal(browser.LING_BROWSER_BRIDGE_PORT, "17323");
  const noBrowser = buildSidecarEnv(
    {},
    { ...process.env, LING_BROWSER_BRIDGE_PORT: "9999" },
  );
  assert.equal(noBrowser.LING_BROWSER_BRIDGE_PORT, undefined);

  const computer = buildSidecarEnv(
    {
      computerUseEnabled: false,
      computerSessionTtlSeconds: 1200,
      computerSessionIdleSeconds: 45,
      computerDeniedApps: "slack.exe,discord.exe",
      computerAuditEnabled: false,
    },
    { ...process.env, LING_COMPUTER_ENABLED: "1" },
  );
  assert.equal(computer.LING_COMPUTER_ENABLED, "0");
  assert.equal(computer.LING_COMPUTER_SESSION_TTL_SECONDS, "1200");
  assert.equal(computer.LING_COMPUTER_SESSION_IDLE_SECONDS, "45");
  assert.equal(
    computer.LING_COMPUTER_DENIED_APPS,
    "slack.exe,discord.exe",
  );
  assert.equal(computer.LING_COMPUTER_AUDIT_ENABLED, "0");
  const noComputer = buildSidecarEnv(
    {},
    {
      ...process.env,
      LING_COMPUTER_ENABLED: "1",
      LING_COMPUTER_AUDIT_ENABLED: "1",
    },
  );
  assert.equal(noComputer.LING_COMPUTER_ENABLED, undefined);
  assert.equal(noComputer.LING_COMPUTER_AUDIT_ENABLED, undefined);

  const enabledComputer = buildSidecarEnv(
    { computerUseEnabled: true, computerAuditEnabled: true },
    process.env,
  );
  assert.equal(enabledComputer.LING_COMPUTER_ENABLED, "1");
  assert.equal(enabledComputer.LING_COMPUTER_AUDIT_ENABLED, "1");

  const embedded = buildSidecarEnv(
    {
      embeddingModel: "text-embedding-v4",
      embeddingBaseUrl:
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
      embeddingApiKey: "sk-embed",
    },
    {
      ...process.env,
      LING_EMBEDDING_MODEL: "stale-model",
      LING_EMBEDDING_BASE_URL: "https://stale.test",
      LING_EMBEDDING_API_KEY: "stale-key",
    },
  );
  assert.equal(embedded.LING_EMBEDDING_MODEL, "text-embedding-v4");
  assert.equal(
    embedded.LING_EMBEDDING_BASE_URL,
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
  );
  assert.equal(embedded.LING_EMBEDDING_API_KEY, "sk-embed");

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
    requestTimeoutMs: 20_000,
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

    const indexRoot = mkdtempSync(join(tmpdir(), "ling-index-"));
    try {
      writeFileSync(
        join(indexRoot, "a.py"),
        "def load_skill():\n    return 1\n",
        "utf8",
      );
      const stats = await client.call<{
        files: number;
        chunks: number;
        vectors: number;
        embedded: number;
        model: string | null;
      }>("index.build", { workspaceRoot: indexRoot });
      assert.equal(stats.files, 1);
      assert.equal(stats.chunks, 1);
      assert.equal(stats.vectors, 0);
      assert.equal(stats.embedded, 0);
      assert.equal(stats.model, null);

      const status = await client.call<{
        files: number;
        totalFiles: number;
        percent: number;
        model: string | null;
      }>("index.status", { workspaceRoot: indexRoot });
      assert.equal(status.files, 1);
      assert.equal(status.totalFiles, 1);
      assert.equal(status.percent, 100);
      assert.equal(status.model, null);
    } finally {
      rmSync(indexRoot, { recursive: true, force: true });
    }

    await client.stop();
    assert.equal(client.isRunning, false);

    const embeddingRoot = mkdtempSync(
      join(tmpdir(), "ling-index-embed-"),
    );
    const embeddingClient = new SidecarClient({
      extensionPath: process.cwd(),
      requestTimeoutMs: 20_000,
      embeddingModel: "text-embedding-v4",
      embeddingBaseUrl:
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
      embeddingApiKey: "sk-test",
      output: (line) => lines.push(line),
    });
    try {
      await embeddingClient.start();
      const status = await embeddingClient.call<{
        model: string | null;
      }>("index.status", { workspaceRoot: embeddingRoot });
      assert.equal(status.model, "text-embedding-v4");
    } finally {
      await embeddingClient.stop();
      rmSync(embeddingRoot, { recursive: true, force: true });
    }

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

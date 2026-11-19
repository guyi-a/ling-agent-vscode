import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { render } from "ink-testing-library";

import type { SidecarClient } from "../rpc.js";
import type {
  BackgroundTask,
  HistorySession,
  JsonObject,
  SessionSnapshot,
  UiEvent,
} from "../types.js";
import { App } from "../tui/App.js";

class FakeClient extends EventEmitter {
  readonly calls: Array<{ method: string; params: JsonObject }> = [];

  constructor(
    private readonly snapshot: SessionSnapshot,
    private readonly history: HistorySession[] = [],
    private readonly tasks: BackgroundTask[] = [],
  ) {
    super();
  }

  async call<T = unknown>(
    method: string,
    params: JsonObject = {},
  ): Promise<T> {
    this.calls.push({ method, params });
    switch (method) {
      case "session.snapshot":
      case "session.open":
        return this.snapshot as T;
      case "session.start":
        return { accepted: true } as T;
      case "session.list":
      case "session.search":
        return { sessions: this.history } as T;
      case "background.list":
        return { tasks: this.tasks } as T;
      case "background.stop":
        return { task: this.tasks[0] ?? {} } as T;
      case "session.queued.sendNow":
        return { accepted: true } as T;
      case "approval.resolve":
      case "question.answer":
        return { resolved: true } as T;
      case "session.setMode":
        return { mode: params.mode } as T;
      case "session.new":
        return { accepted: true } as T;
      case "session.cancel":
        return { cancelled: true } as T;
      case "mcp.status":
        return {
          configPath: "C:\\Users\\ASUS\\AppData\\Roaming\\Ling\\mcp.json",
          servers: [
            {
              name: "chatcut_desktop",
              kind: "stdio",
              target: "npx chatcut-mcp",
              enabled: true,
              state: "connected",
              auth: "none",
              toolCount: 2,
              tools: ["browse_assets", "inspect_asset"],
            },
          ],
          issues: [{ name: "demo", message: "not configured" }],
        } as T;
      case "index.status":
        return {
          files: 3,
          totalFiles: 4,
          chunks: 10,
          vectors: 0,
          indexedFiles: 3,
          percent: 75,
          model: null,
        } as T;
      case "index.build":
        return {
          files: 4,
          totalFiles: 4,
          chunks: 12,
          vectors: 0,
          indexedFiles: 4,
          percent: 100,
          model: null,
          embedded: 12,
        } as T;
      case "changes.get":
        return {
          runId: params.runId,
          changes: [
            {
              path: "src/app.ts",
              beforeExists: true,
              before: "old",
              afterExists: true,
              after: "new",
              added: 2,
              removed: 1,
            },
          ],
        } as T;
      default:
        return {} as T;
    }
  }

  onEvent(listener: (event: UiEvent) => void): () => void {
    this.on("event", listener);
    return () => this.off("event", listener);
  }

  emitEvent(event: UiEvent): void {
    this.emit("event", event);
  }
}

function snapshot(
  overrides: Partial<SessionSnapshot> = {},
): SessionSnapshot {
  return {
    phase: "idle",
    entries: [],
    pendingApprovals: [],
    pendingQuestions: [],
    queuedMessages: [],
    backgroundTasks: [],
    computerSession: null,
    error: null,
    approvalMode: "manual",
    ...overrides,
  };
}

function renderApp(
  fake: FakeClient,
  state: SessionSnapshot,
  workspace = "E:\\probe",
) {
  return render(
    <App
      client={fake as unknown as SidecarClient}
      workspace={workspace}
      model="deepseek-flash"
      initialMode="manual"
      initialEffort="low"
      initialSnapshot={state}
    />,
  );
}

function delay(ms = 60): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("renders the initial conversation", () => {
  const state = snapshot({
    entries: [
      {
        kind: "message",
        message: { id: "u1", role: "user", content: "hello" },
      },
      {
        kind: "message",
        message: { id: "a1", role: "assistant", content: "hi there" },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /User:/);
  assert.match(frame, /Ling:/);
  assert.match(frame, /hello/);
  assert.match(frame, /hi there/);
  assert.doesNotMatch(frame, /\bYou\b/);
  app.unmount();
});

test("hides the tool call id on tool cards", () => {
  const state = snapshot({
    entries: [
      {
        kind: "tool",
        tool: {
          id: "call_00_Bs47abcdef",
          action: "Skill",
          target: "ling-self",
          status: "Completed",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /ling-self/);
  assert.match(frame, /Completed/);
  assert.doesNotMatch(frame, /call_00/);
  assert.doesNotMatch(frame, /#call/);
  app.unmount();
});

test("toggles the latest thinking block with Ctrl+Alt+T", async () => {
  const state = snapshot({
    entries: [
      {
        kind: "message",
        runId: "run-think",
        message: {
          id: "a1",
          role: "assistant",
          content: "answer",
          thinking: "secret reasoning",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  const collapsed = app.lastFrame() ?? "";
  assert.doesNotMatch(collapsed, /secret reasoning/);
  assert.match(collapsed, /Ling: +answer/);
  app.stdin.write(String.fromCharCode(27, 20));
  await delay(100);
  const expanded = app.lastFrame() ?? "";
  assert.match(expanded, /Ling:\s*\n\s+✻ Thinking/);
  assert.match(expanded, /secret reasoning/);
  assert.match(expanded, /answer/);
  app.stdin.write(String.fromCharCode(27, 20));
  await delay(100);
  const recollapsed = app.lastFrame() ?? "";
  assert.doesNotMatch(recollapsed, /secret reasoning/);
  assert.match(recollapsed, /Ling: +answer/);
  app.unmount();
});

test("attaches a file to the next message", async () => {
  const directory = await mkdtemp(
    path.join(os.tmpdir(), "ling-cli-file-"),
  );
  const file = path.join(directory, "note.txt");
  await writeFile(file, "hello", "utf8");
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state, directory);
  app.stdin.write(`/attach ${file}`);
  await delay(40);
  app.stdin.write("\r");
  await delay(150);
  assert.match(app.lastFrame() ?? "", /Attached: note\.txt/);
  app.stdin.write("use this");
  await delay(40);
  app.stdin.write("\r");
  await delay(150);
  const start = fake.calls.find(
    (call) => call.method === "session.start",
  );
  assert.ok(start);
  assert.deepEqual(start.params.attachments, [
    {
      relativePath: file,
      name: "note.txt",
      mediaType: "text/plain",
      kind: "file",
    },
  ]);
  app.unmount();
  await rm(directory, { recursive: true, force: true });
});

test("copies an image attachment into the workspace", async () => {
  const directory = await mkdtemp(
    path.join(os.tmpdir(), "ling-cli-image-"),
  );
  const file = path.join(directory, "pixel.png");
  const bytes = Buffer.from([0x89, 0x50, 0x4e, 0x47]);
  await writeFile(file, bytes);
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state, directory);
  app.stdin.write(`/attach ${file}`);
  await delay(40);
  app.stdin.write("\r");
  await delay(150);
  assert.match(app.lastFrame() ?? "", /Attached: pixel\.png/);
  app.stdin.write("look");
  await delay(40);
  app.stdin.write("\r");
  await delay(150);
  const start = fake.calls.find(
    (call) => call.method === "session.start",
  );
  assert.ok(start);
  const attachment = (
    start.params.attachments as Array<Record<string, unknown>>
  )[0];
  assert.equal(attachment.kind, "image");
  assert.equal(attachment.name, "pixel.png");
  assert.equal(attachment.mediaType, "image/png");
  const relativePath = String(attachment.relativePath);
  assert.match(
    relativePath,
    /^\.agents\/attachments\/pixel-[0-9a-f]{8}\.png$/,
  );
  const copied = path.join(directory, ...relativePath.split("/"));
  assert.deepEqual(await readFile(copied), bytes);
  app.unmount();
  await rm(directory, { recursive: true, force: true });
});

test("shows only the latest run's thinking when expanded", async () => {
  const state = snapshot({
    entries: [
      {
        kind: "message",
        runId: "run-old",
        message: { id: "u1", role: "user", content: "old" },
      },
      {
        kind: "message",
        runId: "run-old",
        message: {
          id: "a1",
          role: "assistant",
          content: [{ type: "tool_call" }],
          thinking: "old thoughts",
        },
      },
      {
        kind: "tool",
        runId: "run-old",
        tool: {
          id: "t1",
          action: "Read",
          target: "old.txt",
          status: "Completed",
        },
      },
      {
        kind: "message",
        runId: "run-old",
        message: { id: "a2", role: "assistant", content: "old answer" },
      },
      {
        kind: "message",
        runId: "run-new",
        message: { id: "u2", role: "user", content: "new" },
      },
      {
        kind: "message",
        runId: "run-new",
        message: {
          id: "a3",
          role: "assistant",
          content: [{ type: "tool_call" }],
          thinking: "new step",
        },
      },
      {
        kind: "tool",
        runId: "run-new",
        tool: {
          id: "t2",
          action: "Read",
          target: "new.txt",
          status: "Completed",
        },
      },
      {
        kind: "message",
        runId: "run-new",
        message: { id: "a4", role: "assistant", content: "new answer" },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write(String.fromCharCode(27, 20));
  await delay(100);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /new step/);
  assert.match(frame, /new answer/);
  assert.doesNotMatch(frame, /old thoughts/);
  assert.equal((frame.match(/✻ Thinking/g) ?? []).length, 1);
  app.unmount();
});

test("edits Ling config with /config and masks secrets", async () => {
  const appData = await mkdtemp(
    path.join(os.tmpdir(), "ling-cli-config-"),
  );
  const previous = process.env.APPDATA;
  process.env.APPDATA = appData;
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  try {
    app.stdin.write("/config");
    await delay(40);
    app.stdin.write("\r");
    await delay(160);
    const initial = app.lastFrame() ?? "";
    assert.match(initial, /Configuration/);
    assert.match(initial, /apiKey/);
    assert.match(initial, /model/);
    assert.match(initial, /deepseek-flash/);

    app.stdin.write("\r");
    await delay(80);
    app.stdin.write("sk-test-secret");
    await delay(60);
    const editing = app.lastFrame() ?? "";
    assert.doesNotMatch(editing, /sk-test-secret/);
    app.stdin.write("\r");
    await delay(160);

    app.stdin.write(String.fromCharCode(27, 91, 66));
    await delay(60);
    app.stdin.write("\r");
    await delay(80);
    app.stdin.write(String.fromCharCode(127).repeat(14));
    await delay(60);
    app.stdin.write("test-model");
    await delay(60);
    app.stdin.write("\r");
    await delay(160);

    const configPath = path.join(appData, "Ling", "config.json");
    const saved = JSON.parse(
      await readFile(configPath, "utf8"),
    ) as Record<string, unknown>;
    assert.equal(saved.apiKey, "sk-test-secret");
    assert.equal(saved.model, "test-model");
    assert.equal(saved.baseUrl, "https://api.deepseek.com/v1");
    assert.equal(saved.version, 1);
    assert.match(app.lastFrame() ?? "", /Saved model/);
  } finally {
    app.unmount();
    if (previous === undefined) {
      delete process.env.APPDATA;
    } else {
      process.env.APPDATA = previous;
    }
    await rm(appData, { recursive: true, force: true });
  }
});

test("groups a tool run under one Ling turn", () => {
  const state = snapshot({
    entries: [
      {
        kind: "message",
        runId: "run-1",
        message: { id: "u1", role: "user", content: "go" },
      },
      {
        kind: "tool",
        runId: "run-1",
        tool: {
          id: "t1",
          action: "Skill",
          target: "inline-visualization",
          status: "Completed",
        },
      },
      {
        kind: "tool",
        runId: "run-1",
        tool: {
          id: "t2",
          action: "Skill",
          target: "visualize",
          status: "Completed",
        },
      },
      {
        kind: "message",
        runId: "run-1",
        message: { id: "a1", role: "assistant", content: "done" },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  const frame = app.lastFrame() ?? "";
  assert.equal((frame.match(/Ling:/g) ?? []).length, 1);
  assert.match(frame, /Ling:\s*\n\s+Skill inline-visualization/);
  assert.match(frame, /^\s+done$/m);
  app.unmount();
});

test("puts the assistant label on the same line as the answer", () => {
  const state = snapshot({
    entries: [
      {
        kind: "message",
        runId: "run-text",
        message: { id: "a1", role: "assistant", content: "DONE" },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  assert.match(app.lastFrame() ?? "", /Ling: +DONE/);
  app.unmount();
});

test("shows slash commands and runs the selected one", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/");
  await delay(60);
  const menu = app.lastFrame() ?? "";
  assert.match(menu, /\/help/);
  assert.match(menu, /Enter run/);
  app.stdin.write("\r");
  await delay(100);
  assert.match(app.lastFrame() ?? "", /Commands:/);
  app.unmount();
});

test("completes a slash command with Tab", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/his");
  await delay(60);
  app.stdin.write("\t");
  await delay(60);
  assert.match(app.lastFrame() ?? "", /\/history/);
  app.stdin.write("\r");
  await delay(120);
  assert.ok(
    fake.calls.some((call) => call.method === "session.list"),
  );
  app.unmount();
});

test("shows MCP status", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/mcp");
  await delay(40);
  app.stdin.write("\r");
  await delay(120);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /MCP status/);
  assert.match(frame, /chatcut_desktop/);
  assert.match(frame, /connected/);
  assert.match(frame, /2 tools/);
  assert.match(frame, /demo: not configured/);
  assert.ok(
    fake.calls.some((call) => call.method === "mcp.status"),
  );
  app.unmount();
});

test("shows code index status", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/index-status");
  await delay(40);
  app.stdin.write("\r");
  await delay(120);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /Code index/);
  assert.match(frame, /3\/4 · 75%/);
  assert.match(frame, /BM25 only/);
  app.unmount();
});

test("builds the code index and shows progress", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/index");
  await delay(40);
  app.stdin.write("\r");
  await delay(150);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /Code index/);
  assert.match(frame, /4\/4 · 100%/);
  assert.match(frame, /embedded 12/);
  assert.ok(
    fake.calls.some((call) => call.method === "index.build"),
  );
  app.unmount();
});

test("shows the latest run's file changes", async () => {
  const state = snapshot({
    sessionId: "session-1",
    entries: [
      {
        kind: "tool",
        runId: "run-1",
        tool: {
          id: "t1",
          action: "Edit",
          target: "src/app.ts",
          status: "Completed",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/changes");
  await delay(40);
  app.stdin.write("\r");
  await delay(120);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /Changes · 1 file/);
  assert.match(frame, /src\/app\.ts/);
  assert.match(frame, /\+2 −1/);
  const call = fake.calls.find(
    (item) => item.method === "changes.get",
  );
  assert.ok(call);
  assert.deepEqual(call.params, {
    sessionId: "session-1",
    runId: "run-1",
  });
  app.unmount();
});

test("cycles approval mode with Shift+Tab", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("[Z");
  await delay(100);
  const changed = fake.calls.find(
    (call) => call.method === "session.setMode",
  );
  assert.ok(changed);
  assert.equal(changed.params.mode, "accept-write");
  app.unmount();
});

test("submits a prompt on Enter", async () => {
  const state = snapshot();
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("hello");
  await delay(30);
  app.stdin.write("\r");
  await delay(80);
  const start = fake.calls.find((call) => call.method === "session.start");
  assert.ok(start);
  assert.equal(start.params.prompt, "hello");
  assert.equal(start.params.workspaceRoot, "E:\\probe");
  assert.equal(start.params.approvalMode, "manual");
  assert.equal(start.params.reasoning, "low");
  app.unmount();
});

test("allows an approval with key 1", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingApprovals: [
      {
        id: "approval-1",
        tool: "write_file",
        action: "Edit",
        target: "a.txt",
        reason: "write E:\\probe\\a.txt",
        arguments: { path: "a.txt", content: "hello" },
        effectDigest: "digest-1",
        allowAlways: {
          kind: "mode",
          mode: "accept-write",
          label: "Yes, allow all edits this session",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("1");
  await delay(80);
  const resolved = fake.calls.find(
    (call) => call.method === "approval.resolve",
  );
  assert.ok(resolved);
  assert.deepEqual(resolved.params, {
    approvalId: "approval-1",
    effectDigest: "digest-1",
    approved: true,
    remember: false,
    feedback: null,
  });
  app.unmount();
});

test("answers a single-select question with a number", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingQuestions: [
      {
        callId: "question-1",
        questions: [
          {
            id: "q1",
            question: "Pick a color",
            options: [{ label: "Red" }, { label: "Blue" }],
            multiSelect: false,
          },
        ],
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("2");
  await delay(80);
  const answered = fake.calls.find(
    (call) => call.method === "question.answer",
  );
  assert.ok(answered);
  assert.equal(answered.params.callId, "question-1");
  assert.deepEqual(answered.params.answers, [
    { id: "q1", selected: ["Blue"] },
  ]);
  app.unmount();
});

test("renders streamed assistant deltas", async () => {
  const state = snapshot({
    phase: "running",
    entries: [
      {
        kind: "message",
        message: {
          id: "a1",
          role: "assistant",
          content: "",
          streaming: true,
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  fake.emitEvent({
    type: "message.delta",
    messageId: "a1",
    role: "assistant",
    delta: "Hel",
  });
  await delay(60);
  assert.match(app.lastFrame() ?? "", /Hel/);
  app.unmount();
});

test("renders assistant markdown without raw markers", () => {
  const state = snapshot({
    entries: [
      {
        kind: "message",
        message: {
          id: "a1",
          role: "assistant",
          content: "**bold** and `code`\n\n- item one",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /bold/);
  assert.match(frame, /code/);
  assert.match(frame, /item one/);
  assert.doesNotMatch(frame, /\*\*bold\*\*/);
  app.unmount();
});

test("denies an approval with feedback", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingApprovals: [
      {
        id: "approval-feedback",
        tool: "run_command",
        action: "Run",
        target: "echo nope",
        effectDigest: "digest-feedback",
        arguments: { command: "echo nope" },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("4");
  await delay(40);
  app.stdin.write("not now");
  await delay(40);
  app.stdin.write("\r");
  await delay(100);
  const resolved = fake.calls.find(
    (call) => call.method === "approval.resolve",
  );
  assert.ok(resolved);
  assert.equal(resolved.params.approved, false);
  assert.equal(resolved.params.feedback, "not now");
  app.unmount();
});

test("allowAlways mode switches approval mode before resolving", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingApprovals: [
      {
        id: "approval-mode",
        tool: "write_file",
        target: "a.txt",
        effectDigest: "digest-mode",
        allowAlways: {
          kind: "mode",
          mode: "accept-write",
          label: "Yes, allow all edits this session",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("2");
  await delay(120);
  const setModeIndex = fake.calls.findIndex(
    (call) => call.method === "session.setMode",
  );
  const resolveIndex = fake.calls.findIndex(
    (call) => call.method === "approval.resolve",
  );
  assert.ok(setModeIndex >= 0);
  assert.ok(resolveIndex > setModeIndex);
  assert.equal(
    fake.calls[setModeIndex].params.mode,
    "accept-write",
  );
  app.unmount();
});

test("allowAlways command resolves with remember", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingApprovals: [
      {
        id: "approval-command",
        tool: "run_command",
        target: "echo ok",
        effectDigest: "digest-command",
        allowAlways: {
          kind: "command",
          label: "Yes, and don't ask again",
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("2");
  await delay(100);
  const resolved = fake.calls.find(
    (call) => call.method === "approval.resolve",
  );
  assert.ok(resolved);
  assert.equal(resolved.params.remember, true);
  app.unmount();
});

test("supports multi-select questions", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingQuestions: [
      {
        callId: "question-multi",
        questions: [
          {
            id: "q-multi",
            question: "Pick letters",
            options: [{ label: "A" }, { label: "B" }, { label: "C" }],
            multiSelect: true,
          },
        ],
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("1");
  await delay(50);
  app.stdin.write("3");
  await delay(50);
  app.stdin.write("\r");
  await delay(120);
  const answered = fake.calls.find(
    (call) => call.method === "question.answer",
  );
  assert.ok(answered);
  assert.deepEqual(answered.params.answers, [
    { id: "q-multi", selected: ["A", "C"] },
  ]);
  app.unmount();
});

test("answers multiple questions in order", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingQuestions: [
      {
        callId: "question-many",
        questions: [
          {
            id: "q1",
            question: "First?",
            options: [{ label: "One" }, { label: "Two" }],
          },
          {
            id: "q2",
            question: "Second?",
            options: [{ label: "Three" }, { label: "Four" }],
          },
        ],
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("1");
  await delay(70);
  app.stdin.write("2");
  await delay(150);
  const answered = fake.calls.find(
    (call) => call.method === "question.answer",
  );
  assert.ok(answered);
  assert.deepEqual(answered.params.answers, [
    { id: "q1", selected: ["One"] },
    { id: "q2", selected: ["Four"] },
  ]);
  app.unmount();
});

test("supports a custom question answer", async () => {
  const state = snapshot({
    phase: "waiting_approval",
    pendingQuestions: [
      {
        callId: "question-custom",
        questions: [
          {
            id: "q-custom",
            question: "Describe it",
            options: [],
            multiSelect: false,
          },
        ],
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("custom answer");
  await delay(80);
  app.stdin.write("\r");
  await delay(140);
  const answered = fake.calls.find(
    (call) => call.method === "question.answer",
  );
  assert.ok(answered);
  assert.deepEqual(answered.params.answers, [
    { id: "q-custom", selected: [], custom: "custom answer" },
  ]);
  app.unmount();
});

test("expands the last tool with /last", async () => {
  const state = snapshot({
    entries: [
      {
        kind: "tool",
        tool: {
          id: "tool-1",
          name: "edit_file",
          action: "Edit",
          target: "a.txt",
          status: "Completed",
          detail: {
            kind: "diff",
            rows: [
              { type: "remove", oldLine: 1, text: "old line" },
              { type: "add", newLine: 1, text: "new line" },
            ],
            added: 1,
            removed: 1,
            truncated: false,
          },
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/last");
  await delay(40);
  app.stdin.write("\r");
  await delay(100);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /old line/);
  assert.match(frame, /new line/);
  app.unmount();
});

test("opens tool details by recency index", async () => {
  const state = snapshot({
    entries: [
      {
        kind: "tool",
        tool: {
          id: "tool-old",
          action: "Read",
          target: "old.txt",
          status: "Completed",
          detail: { kind: "io", input: "old input", output: "old output" },
        },
      },
      {
        kind: "tool",
        tool: {
          id: "tool-new",
          action: "Read",
          target: "new.txt",
          status: "Completed",
          detail: { kind: "io", input: "new input", output: "new output" },
        },
      },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  app.stdin.write("/details 1");
  await delay(40);
  app.stdin.write("\r");
  await delay(100);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /new output/);
  assert.doesNotMatch(frame, /old output/);
  app.unmount();
});

test("lists history sessions", async () => {
  const state = snapshot();
  const history = [
    {
      id: "session-1234567890",
      title: "Existing talk",
      workspaceRoot: "E:\\probe",
      updatedAt: 1_700_000_000_000,
      status: "idle",
    },
  ];
  const fake = new FakeClient(state, history);
  const app = renderApp(fake, state);
  app.stdin.write("/history");
  await delay(40);
  app.stdin.write("\r");
  await delay(160);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /Existing talk/);
  assert.match(frame, /Enter open/);
  assert.match(frame, /❯ .*Existing talk/);
  app.unmount();
});

test("opens the selected history session with arrows and Enter", async () => {
  const state = snapshot();
  const history = [
    {
      id: "session-aaaa1111",
      title: "Older talk",
      workspaceRoot: "E:\\probe",
      updatedAt: 1_700_000_000_000,
      status: "idle",
    },
    {
      id: "session-bbbb2222",
      title: "Newer talk",
      workspaceRoot: "E:\\probe",
      updatedAt: 1_700_000_100_000,
      status: "idle",
    },
  ];
  const fake = new FakeClient(state, history);
  const app = renderApp(fake, state);
  app.stdin.write("/history");
  await delay(40);
  app.stdin.write("\r");
  await delay(160);
  assert.match(app.lastFrame() ?? "", /Older talk/);
  app.stdin.write(String.fromCharCode(27, 91, 66));
  await delay(80);
  app.stdin.write("\r");
  await delay(160);
  const opened = fake.calls.find(
    (call) => call.method === "session.open",
  );
  assert.ok(opened);
  assert.equal(opened.params.sessionId, "session-bbbb2222");
  app.unmount();
});

test("shows queued messages inline, newest first", () => {
  const state = snapshot({
    phase: "running",
    queuedMessages: [
      { id: "queued-1", text: "first queued", attachmentCount: 0 },
      { id: "queued-2", text: "second queued", attachmentCount: 0 },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  const frame = app.lastFrame() ?? "";
  assert.match(frame, /Queued · 2/);
  assert.match(frame, /first queued/);
  assert.match(frame, /second queued/);
  assert.ok(
    frame.indexOf("second queued") < frame.indexOf("first queued"),
  );
  app.unmount();
});

test("Esc interrupts and promotes the queued message", async () => {
  const state = snapshot({
    phase: "running",
    queuedMessages: [
      { id: "queued-1", text: "older", attachmentCount: 0 },
      { id: "queued-2", text: "newest", attachmentCount: 0 },
    ],
  });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("");
  await delay(120);
  const promoted = fake.calls.find(
    (call) => call.method === "session.queued.sendNow",
  );
  assert.ok(promoted);
  assert.equal(promoted.params.queuedId, "queued-2");
  assert.equal(
    fake.calls.some((call) => call.method === "session.cancel"),
    false,
  );
  app.unmount();
});

test("Esc cancels the run when the queue is empty", async () => {
  const state = snapshot({ phase: "running" });
  const fake = new FakeClient(state);
  const app = renderApp(fake, state);
  await delay(30);
  app.stdin.write("");
  await delay(120);
  assert.ok(
    fake.calls.some((call) => call.method === "session.cancel"),
  );
  assert.equal(
    fake.calls.some(
      (call) => call.method === "session.queued.sendNow",
    ),
    false,
  );
  app.unmount();
});

test("lists background tasks and stops one", async () => {
  const state = snapshot({
    backgroundTasks: [
      {
        id: "task-1",
        description: "build",
        status: "running",
      },
    ],
  });
  const tasks = [
    { id: "task-1", description: "build", status: "running" },
  ];
  const fake = new FakeClient(state, [], tasks);
  const app = renderApp(fake, state);
  app.stdin.write("/tasks");
  await delay(40);
  app.stdin.write("\r");
  await delay(100);
  assert.match(app.lastFrame() ?? "", /build/);
  app.stdin.write("/stop-task task-1");
  await delay(40);
  app.stdin.write("\r");
  await delay(100);
  const stopped = fake.calls.find(
    (call) => call.method === "background.stop",
  );
  assert.ok(stopped);
  assert.equal(stopped.params.taskId, "task-1");
  app.unmount();
});

import assert from "node:assert/strict";
import {
  MOCK_EVENTS,
  APPROVAL_EDIT,
  APPROVAL_RUN,
} from "../webview/src/mockData";
import type { UiEvent } from "../webview/src/protocol";
import {
  createEmptyTimelineState,
  timelineReducer,
} from "../webview/src/state/timelineReducer";
import {
  classifyToolCategory,
  formatRunDuration,
  groupTimelineEntries,
} from "../webview/src/state/timelineBlocks";
import type {
  TimelineEntry,
  TimelineState,
} from "../webview/src/types";
import {
  isHostToWebviewMessage,
  isUiEvent,
  isWebviewToHostMessage,
  UI_PROTOCOL_VERSION,
} from "../shared/transport";
import { toAttachmentRefs } from "../shared/attachments";

function apply(state: TimelineState, event: UiEvent): TimelineState {
  return timelineReducer(state, event);
}

function snapshotEvent(): UiEvent {
  const first = MOCK_EVENTS[0]?.event;
  assert.ok(first && first.type === "session.snapshot");
  return first;
}

let state = apply(createEmptyTimelineState(), snapshotEvent());
assert.equal(state.phase, "running");
assert.equal(state.entries.length, 5);
assert.equal(state.pendingApprovals.length, 0);

let traceState = apply(createEmptyTimelineState(), {
  type: "subagent.trace",
  callId: "call-1",
  entry: {
    kind: "assistant",
    id: "assistant-1",
    text: "Found it.",
  },
});
traceState = apply(traceState, {
  type: "subagent.trace",
  callId: "call-1",
  entry: {
    kind: "tool_result",
    id: "read-1",
    name: "read_file",
    output: "content",
    isError: false,
  },
});
assert.equal(traceState.subagentTraces["call-1"].length, 2);
assert.equal(
  traceState.subagentTraces["call-1"][0].kind,
  "assistant",
);

let thinkingState = apply(createEmptyTimelineState(), {
  type: "thinking.delta",
  messageId: "message-thinking-1",
  delta: "Consider ",
});
thinkingState = apply(thinkingState, {
  type: "thinking.delta",
  messageId: "message-thinking-1",
  delta: "the options.",
});
const thinkingEntry = thinkingState.entries.find(
  (entry) =>
    entry.kind === "message" &&
    entry.message.id === "message-thinking-1",
);
assert.equal(thinkingEntry?.kind, "message");
assert.equal(
  thinkingEntry?.kind === "message"
    ? thinkingEntry.message.thinking
    : undefined,
  "Consider the options.",
);
assert.equal(
  thinkingEntry?.kind === "message"
    ? thinkingEntry.message.content
    : undefined,
  "",
);
assert.equal(thinkingState.phase, "running");
assert.equal(
  thinkingEntry?.kind === "message"
    ? thinkingEntry.message.thinkingDone
    : undefined,
  false,
);
assert.equal(
  typeof (thinkingEntry?.kind === "message"
    ? thinkingEntry.message.thinkingStartedAt
    : undefined),
  "number",
);

thinkingState = apply(thinkingState, {
  type: "thinking.completed",
  messageId: "message-thinking-1",
});
const doneEntry = thinkingState.entries.find(
  (entry) =>
    entry.kind === "message" &&
    entry.message.id === "message-thinking-1",
);
assert.equal(
  doneEntry?.kind === "message"
    ? doneEntry.message.thinkingDone
    : undefined,
  true,
);
assert.equal(
  typeof (doneEntry?.kind === "message"
    ? doneEntry.message.thinkingDurationMs
    : undefined),
  "number",
);

thinkingState = apply(thinkingState, {
  type: "thinking.delta",
  messageId: "message-thinking-1",
  delta: " More.",
});
const resumedEntry = thinkingState.entries.find(
  (entry) =>
    entry.kind === "message" &&
    entry.message.id === "message-thinking-1",
);
assert.equal(
  resumedEntry?.kind === "message"
    ? resumedEntry.message.thinkingDone
    : undefined,
  false,
);
assert.equal(
  resumedEntry?.kind === "message"
    ? resumedEntry.message.thinkingDurationMs
    : undefined,
  undefined,
);
assert.equal(
  typeof (resumedEntry?.kind === "message"
    ? resumedEntry.message.thinkingStartedAt
    : undefined),
  "number",
);
assert.equal(
  resumedEntry?.kind === "message"
    ? resumedEntry.message.thinking
    : undefined,
  "Consider the options. More.",
);

state = apply(state, {
  type: "message.delta",
  messageId: "message-assistant-1",
  role: "assistant",
  delta: "First",
});
state = apply(state, {
  type: "message.delta",
  messageId: "message-assistant-1",
  role: "assistant",
  delta: " second",
});

const assistantMessages = state.entries.filter(
  (entry) =>
    entry.kind === "message" &&
    entry.message.id === "message-assistant-1",
);
assert.equal(assistantMessages.length, 1);
assert.equal(
  assistantMessages[0]?.kind === "message"
    ? assistantMessages[0].message.content
    : "",
  "First second",
);

state = apply(state, {
  type: "tool.updated",
  tool: {
    id: "tool-read-session",
    action: "Read",
    target: "src/session.ts",
    status: "Completed",
    details: [],
  },
});
const readTool = state.entries.find(
  (entry) => entry.kind === "tool" && entry.tool.id === "tool-read-session",
);
assert.equal(
  readTool?.kind === "tool" ? readTool.tool.status : undefined,
  "Completed",
);

state = apply(state, {
  type: "approval.requested",
  request: APPROVAL_EDIT,
});
state = apply(state, {
  type: "approval.requested",
  request: APPROVAL_RUN,
});
assert.equal(state.phase, "waiting_approval");
assert.deepEqual(
  state.pendingApprovals.map((request) => request.id),
  [APPROVAL_EDIT.id, APPROVAL_RUN.id],
);

state = apply(state, {
  type: "approval.resolved",
  approvalId: APPROVAL_EDIT.id,
  approved: true,
});
assert.equal(state.pendingApprovals[0]?.id, APPROVAL_RUN.id);
assert.equal(
  state.entries.filter((entry) => entry.kind === "approval").length,
  1,
);

state = apply(state, { type: "run.cancelled" });
assert.equal(state.phase, "cancelled");
assert.equal(state.pendingApprovals.length, 0);
assert.ok(
  state.entries.every(
    (entry) =>
      entry.kind !== "tool" ||
      (entry.tool.status !== "Running" && entry.tool.status !== "Waiting"),
  ),
);

const cancelled = state;
state = apply(state, {
  type: "approval.requested",
  request: APPROVAL_RUN,
});
assert.deepEqual(state, cancelled);

state = apply(apply(createEmptyTimelineState(), snapshotEvent()), {
  type: "run.failed",
  message: "The agent process stopped unexpectedly.",
});
assert.equal(state.phase, "failed");
assert.equal(state.error, "The agent process stopped unexpectedly.");

assert.equal(
  isWebviewToHostMessage({
    type: "ui.ready",
    host: "vscode",
    protocol: UI_PROTOCOL_VERSION,
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "approval.resolve",
    approvalId: APPROVAL_EDIT.id,
    effectDigest: APPROVAL_EDIT.effectDigest,
    approved: true,
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "approval.resolve",
    approvalId: APPROVAL_EDIT.id,
    effectDigest: APPROVAL_EDIT.effectDigest,
    approved: "yes",
  }),
  false,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.ui-event",
    sequence: 1,
    event: snapshotEvent(),
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.host-context",
    workspaceName: "ling-vscode",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({ type: "index.build" }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "plan.build",
    path: ".agents/plans/x.md",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "memory.create",
    scope: "workspace",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "memory.open",
    path: "E:/workspace/.agents/memory/testing.md",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "memory.delete",
    name: "testing",
    scope: "global",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "memory.delete",
    name: "",
    scope: "workspace",
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({ type: "plan.open", path: "" }),
  false,
);
assert.equal(isHostToWebviewMessage({ type: "ling.ready" }), true);
assert.equal(
  isWebviewToHostMessage({ type: "mode.change", mode: "plan" }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.index-result",
    ok: true,
    stats: {
      files: 2,
      totalFiles: 2,
      chunks: 3,
      vectors: 3,
      indexedFiles: 2,
      percent: 100,
      embedded: 3,
      model: "text-embedding-v3",
    },
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.index-status",
    status: {
      files: 2,
      totalFiles: 2,
      chunks: 3,
      vectors: 1,
      indexedFiles: 1,
      percent: 50,
      model: "text-embedding-v3",
    },
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.index-status",
    status: {
      files: 2,
      totalFiles: 2,
      chunks: 3,
      vectors: 1,
      indexedFiles: 1,
      percent: 150,
      model: "text-embedding-v3",
    },
  }),
  false,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.index-result",
    ok: false,
    error: "embedding provider is down",
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.index-result",
    ok: true,
    stats: {
      files: 2,
      totalFiles: 5,
      chunks: 3,
      vectors: 0,
      indexedFiles: 2,
      percent: 40,
      embedded: 0,
      model: null,
    },
  }),
  true,
);
assert.equal(isUiEvent(snapshotEvent()), true);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.ui-event",
    sequence: -1,
    event: snapshotEvent(),
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "history.open",
    sessionId: "session-1",
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.history-list",
    sessions: [
      {
        id: "session-1",
        title: "Earlier work",
        workspaceRoot: "E:/workspace",
        updatedAt: 1,
        status: "idle",
        snippet: "…banana is yellow…",
      },
    ],
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.history-list",
    sessions: [],
    error: "Unable to load conversation history",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "history.search",
    query: "banana",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "history.search",
    query: 1,
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "history.delete",
    sessionId: "session-1",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "history.delete",
    sessionId: "",
  }),
  false,
);

const compactionState = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "idle",
    entries: [
      {
        kind: "compaction",
        compaction: {
          id: "compaction:1",
          compactionId: "compaction-1",
          replacedCount: 3,
        },
      },
    ],
    pendingApprovals: [],
  },
});
assert.equal(compactionState.entries[0]?.kind, "compaction");
assert.equal(
  compactionState.entries[0]?.kind === "compaction"
    ? compactionState.entries[0].compaction.replacedCount
    : undefined,
  3,
);

const imageAttachment = {
  relativePath: ".agents/attachments/pasted-ab12.png",
  name: "pasted-ab12.png",
  mediaType: "image/png",
  uri: "https://example.test/pasted-ab12.png",
};
const imageState = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "idle",
    entries: [
      {
        kind: "message",
        message: {
          id: "message-user-image",
          role: "user",
          content: "describe this",
          streaming: false,
          attachments: [imageAttachment],
        },
      },
    ],
    pendingApprovals: [],
  },
});
const imageEntry = imageState.entries[0];
assert.equal(imageEntry?.kind, "message");
assert.deepEqual(
  imageEntry?.kind === "message"
    ? imageEntry.message.attachments
    : undefined,
  [imageAttachment],
);
assert.notEqual(
  imageEntry?.kind === "message"
    ? imageEntry.message.attachments?.[0]
    : undefined,
  imageAttachment,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-1",
      action: "Edit",
      target: "a.py",
      status: "Completed",
      details: [],
      detail: {
        kind: "diff",
        path: "a.py",
        rows: [
          { type: "remove", oldLine: 1, text: "old" },
          { type: "add", newLine: 1, text: "new" },
        ],
        added: 1,
        removed: 1,
      },
    },
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "changes.open",
    sessionId: "session-1",
    runId: "run-1",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "changes.revert",
    sessionId: "session-1",
    runId: "run-1",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({ type: "mcp.openConfig" }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "mcp.authorize",
    name: "docs",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "mcp.revokeAuth",
    name: "docs",
  }),
  true,
);

assert.equal(
  classifyToolCategory({
    id: "tool-mcp",
    name: "mcp_docs__search",
    action: "MCP",
    target: "docs / search",
    status: "Completed",
    details: [],
  }),
  "mcp",
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-skill",
      action: "Skill",
      target: "inline-visualization",
      status: "Completed",
      details: [],
      detail: {
        kind: "skill",
        name: "inline-visualization",
        description: "Render compact diagrams.",
        body: "# Diagram",
        skillPath: "E:/skills/inline-visualization",
        ok: true,
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-memory",
      action: "Memory",
      target: "workspace:testing",
      status: "Completed",
      details: [],
      detail: {
        kind: "memory",
        operation: "write",
        scope: "workspace",
        name: "testing",
        path: ".agents/memory/testing.md",
        ok: true,
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-web-search",
      action: "Web",
      target: "DeepSeek search API",
      status: "Completed",
      details: [],
      detail: {
        kind: "io",
        input: "DeepSeek search API",
        output: "Search result",
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-2",
      action: "Run",
      target: "python a.py",
      status: "Completed",
      details: [],
      detail: {
        kind: "io",
        input: "python a.py",
        output: "ok",
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-plan",
      action: "Plan",
      target: "Fix login",
      status: "Completed",
      details: [],
      detail: {
        kind: "plan",
        path: ".agents/plans/x.md",
        text: "# Fix login",
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-todo",
      action: "Plan",
      target: "2 todos",
      status: "Completed",
      details: [],
      detail: {
        kind: "todo",
        todos: [
          { id: "1", content: "Read", status: "completed" },
          { id: "2", content: "Plan", status: "in_progress" },
        ],
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-subagent",
      action: "Subagent",
      target: "Find auth",
      status: "Completed",
      details: [],
      detail: {
        kind: "subagent",
        description: "Find auth",
        subagentType: "explore",
        mode: "foreground",
        summary: "Auth lives in src/auth.ts.",
        trace: [
          {
            kind: "tool_call",
            id: "read-1",
            name: "read_file",
            arguments: { path: "src/auth.ts" },
          },
          {
            kind: "tool_result",
            id: "read-1",
            name: "read_file",
            output: "export function auth() {}",
            isError: false,
          },
          {
            kind: "assistant",
            id: "assistant-1",
            text: "Auth lives in src/auth.ts.",
          },
        ],
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-subagent-bad",
      action: "Subagent",
      target: "Find auth",
      status: "Completed",
      details: [],
      detail: {
        kind: "subagent",
        description: "Find auth",
        subagentType: "explore",
        mode: "foreground",
        summary: 42,
      },
    },
  }),
  false,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-browser",
      action: "Browser",
      target: "open_tab https://example.com",
      status: "Completed",
      details: [],
      detail: {
        kind: "io",
        input: '{"action":"open_tab"}',
        output: '{"ok":true}',
      },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "subagent.trace",
    callId: "call-1",
    entry: {
      kind: "tool_call",
      id: "read-1",
      name: "read_file",
      arguments: { path: "a.py" },
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "subagent.trace",
    callId: "call-1",
    entry: {
      kind: "assistant",
      id: "assistant-1",
      text: "Done.",
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "subagent.trace",
    callId: "call-1",
    entry: { kind: "assistant", id: "assistant-1", text: 42 },
  }),
  false,
);
assert.equal(
  isUiEvent({
    type: "subagent.trace",
    callId: "",
    entry: { kind: "error", message: "boom" },
  }),
  false,
);
assert.equal(
  isUiEvent({
    type: "plan.created",
    path: ".agents/plans/x.md",
    sessionId: "session-1",
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "plan.created",
    path: "",
    sessionId: "session-1",
  }),
  false,
);
assert.equal(
  isUiEvent({
    type: "tool.updated",
    tool: {
      id: "tool-detail-3",
      action: "Edit",
      target: "a.py",
      status: "Completed",
      details: [],
      detail: {
        kind: "diff",
        path: "a.py",
        rows: [{ type: "bogus", text: "x" }],
        added: 1,
        removed: 0,
      },
    },
  }),
  false,
);

assert.equal(
  isUiEvent({
    type: "thinking.delta",
    messageId: "message-thinking-1",
    delta: "thinking",
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "thinking.delta",
    messageId: "message-thinking-1",
  }),
  false,
);
assert.equal(
  isUiEvent({
    type: "thinking.completed",
    messageId: "message-thinking-1",
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "thinking.completed",
  }),
  false,
);
assert.equal(
  isUiEvent({
    type: "session.snapshot",
    snapshot: {
      phase: "idle",
      entries: imageState.entries,
      pendingApprovals: [],
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "session.snapshot",
    snapshot: {
      phase: "idle",
      entries: [
        {
          kind: "message",
          runId: "run-with-metadata",
          message: {
            id: "message-with-metadata",
            role: "assistant",
            content: "Done.",
            streaming: false,
          },
        },
      ],
      runs: [
        {
          id: "run-with-metadata",
          status: "completed",
          startedAt: 100,
          completedAt: 200,
        },
      ],
      pendingApprovals: [],
    },
  }),
  true,
);
assert.equal(
  isUiEvent({
    type: "session.snapshot",
    snapshot: {
      phase: "idle",
      entries: [],
      runs: [{ id: "bad-run", status: "stopped" }],
      pendingApprovals: [],
    },
  }),
  false,
);

assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "",
    approvalMode: "manual",
    attachments: [imageAttachment],
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "hello",
    approvalMode: "manual",
    attachments: [],
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "hello",
    approvalMode: "manual",
    attachments: [{ ...imageAttachment, mediaType: "text/plain" }],
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "think hard",
    approvalMode: "manual",
    effort: "high",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "think harder",
    approvalMode: "manual",
    effort: "xhigh",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "think harder",
    approvalMode: "manual",
    effort: "max",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "think harder",
    approvalMode: "manual",
    effort: "turbo",
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "message.delete",
    messageId: "message-assistant-1",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "message.delete",
    messageId: "",
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "message.regenerate",
    messageId: "message-assistant-1",
    effort: "high",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "message.regenerate",
    messageId: "message-assistant-1",
    effort: "turbo",
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "effort.change",
    effort: "medium",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "effort.change",
    effort: "maximum",
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "settings.save",
    apiKey: "sk-test",
    model: "deepseek-chat",
    baseUrl: "https://api.deepseek.com/v1",
    effort: "high",
    diffView: "inline",
    maxSteps: 10,
    maxParallelTools: 4,
    commandTimeoutSeconds: 120,
    approvalTimeoutSeconds: 120,
    contextWindowTokens: 1_000_000,
    keepLastUserTurns: 5,
    embeddingModel: "",
    embeddingBaseUrl: "",
    embeddingApiKey: "",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "settings.save",
    apiKey: "sk-test",
    model: "deepseek-chat",
    baseUrl: "https://api.deepseek.com/v1",
    effort: "high",
    diffView: "inline",
    maxSteps: 0,
    maxParallelTools: 4,
    commandTimeoutSeconds: 120,
    approvalTimeoutSeconds: 120,
    contextWindowTokens: 1_000_000,
    keepLastUserTurns: 5,
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "attachment.pick",
  }),
  true,
);
assert.deepEqual(
  toAttachmentRefs([
    {
      relativePath: ".agents/attachments/doc-ab12.pdf",
      name: "doc.pdf",
      mediaType: "application/pdf",
      kind: "file",
    },
    {
      relativePath: ".agents/attachments/pasted-ab12.png",
      name: "pasted-ab12.png",
      mediaType: "image/png",
      kind: "image",
    },
  ]),
  [
    {
      relativePath: ".agents/attachments/doc-ab12.pdf",
      name: "doc.pdf",
      mediaType: "application/pdf",
      kind: "file",
    },
    {
      relativePath: ".agents/attachments/pasted-ab12.png",
      name: "pasted-ab12.png",
      mediaType: "image/png",
      kind: "image",
    },
  ],
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "read this",
    approvalMode: "manual",
    attachments: [
      {
        relativePath: ".agents/attachments/doc-ab12.pdf",
        name: "doc.pdf",
        mediaType: "application/pdf",
        kind: "file",
      },
    ],
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "read this",
    approvalMode: "manual",
    attachments: [
      {
        relativePath: ".agents/attachments/doc-ab12.pdf",
        name: "doc.pdf",
        mediaType: "application/pdf",
        kind: "other",
      },
    ],
  }),
  false,
);
assert.equal(
  isWebviewToHostMessage({
    type: "attachment.save",
    requestId: "request-1",
    name: "pasted.png",
    mimeType: "image/png",
    dataBase64: "AA==",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "attachment.open",
    relativePath: ".agents/attachments/pasted-ab12.png",
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.attachment-saved",
    requestId: "request-1",
    attachment: imageAttachment,
  }),
  true,
);

const historical = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "idle",
    entries: [],
    pendingApprovals: [],
    sessionId: "session-1",
    readOnly: true,
  },
});
assert.equal(historical.sessionId, "session-1");
assert.equal(historical.readOnly, true);

const questionRequest = {
  callId: "ask-1",
  questions: [
    {
      id: "q1",
      question: "PDF style?",
      options: [
        { label: "Fast" },
        { label: "Pretty", description: "More layout work" },
      ],
      multiSelect: false,
    },
  ],
};

assert.equal(
  isUiEvent({ type: "question.requested", question: questionRequest }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.ui-event",
    sequence: 1,
    event: { type: "question.requested", question: questionRequest },
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "question.answer",
    callId: "ask-1",
    answers: [{ id: "q1", selected: ["Pretty"] }],
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "question.answer",
    callId: "ask-1",
  }),
  false,
);

let questionState = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "running",
    entries: [],
    pendingApprovals: [],
    pendingQuestions: [questionRequest],
  },
});
assert.equal(questionState.pendingQuestions?.length, 1);

questionState = apply(questionState, {
  type: "question.resolved",
  callId: "ask-1",
  answers: [{ id: "q1", selected: ["Pretty"] }],
});
assert.equal(questionState.pendingQuestions?.length, 0);

questionState = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "running",
    entries: [],
    pendingApprovals: [],
    pendingQuestions: [questionRequest],
  },
});
questionState = apply(questionState, { type: "run.cancelled" });
assert.equal(questionState.pendingQuestions?.length, 0);

const queuedMessage = {
  id: "queued-1",
  text: "Send this next",
  attachmentCount: 0,
};
assert.equal(
  isUiEvent({
    type: "session.queue",
    queuedMessages: [queuedMessage],
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "session.queued.sendNow",
    queuedId: "queued-1",
  }),
  true,
);

let queueState = apply(createEmptyTimelineState(), {
  type: "session.queue",
  queuedMessages: [queuedMessage],
});
assert.equal(queueState.queuedMessages?.length, 1);
queueState = apply(queueState, { type: "run.cancelled" });
assert.equal(queueState.queuedMessages?.length, 0);

queueState = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "running",
    entries: [],
    pendingApprovals: [],
    queuedMessages: [queuedMessage],
  },
});
assert.equal(queueState.queuedMessages?.length, 1);

const selection = {
  kind: "editor" as const,
  label: "files.py",
  text: "print('hi')",
  lineCount: 1,
  path: "E:\\workspace\\files.py",
  startLine: 10,
  endLine: 10,
};
assert.equal(
  isWebviewToHostMessage({
    type: "chat.submit",
    text: "What does this do?",
    approvalMode: "manual",
    selections: [selection],
  }),
  true,
);
assert.equal(
  isHostToWebviewMessage({
    type: "ling.selection-added",
    selection: { id: "selection-1", ...selection },
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "selection.capture",
    source: "any",
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "selection.open",
    path: "E:\\workspace\\files.py",
    startLine: 10,
  }),
  true,
);
assert.equal(
  isWebviewToHostMessage({
    type: "selection.copy",
    text: "print('hi')",
  }),
  true,
);

const selectionState = apply(createEmptyTimelineState(), {
  type: "session.snapshot",
  snapshot: {
    phase: "idle",
    entries: [
      {
        kind: "message",
        message: {
          id: "message-1",
          role: "user",
          content: "What does this do?",
          streaming: false,
          selections: [selection],
        },
      },
    ],
    pendingApprovals: [],
  },
});
assert.equal(
  selectionState.entries[0]?.kind === "message"
    ? selectionState.entries[0].message.selections?.length
    : 0,
  1,
);

const mergedBlocks = groupTimelineEntries([
  {
    kind: "message",
    message: {
      id: "u1",
      role: "user",
      content: "hi",
      streaming: false,
    },
  },
  {
    kind: "message",
    message: {
      id: "a1",
      role: "assistant",
      content: "",
      thinking: "one",
      thinkingDone: true,
      thinkingDurationMs: 1000,
      streaming: false,
    },
  },
  {
    kind: "tool",
    tool: {
      id: "t1",
      action: "Read",
      target: "a.py",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "message",
    message: {
      id: "a2",
      role: "assistant",
      content: "",
      thinking: "two",
      thinkingDone: true,
      thinkingDurationMs: 2000,
      streaming: false,
    },
  },
  {
    kind: "message",
    message: {
      id: "a3",
      role: "assistant",
      content: "done",
      streaming: false,
    },
  },
  {
    kind: "message",
    message: {
      id: "u2",
      role: "user",
      content: "again",
      streaming: false,
    },
  },
  {
    kind: "message",
    message: {
      id: "a4",
      role: "assistant",
      content: "",
      thinking: "three",
      thinkingDone: false,
      streaming: true,
    },
  },
] as TimelineEntry[]);

const thinkingBlocks = mergedBlocks.filter(
  (block) => block.kind === "run-thinking",
);
assert.equal(thinkingBlocks.length, 2);
assert.equal(
  thinkingBlocks[0]?.kind === "run-thinking"
    ? thinkingBlocks[0].thinking
    : "",
  "one\n\ntwo",
);
assert.equal(
  thinkingBlocks[0]?.kind === "run-thinking"
    ? thinkingBlocks[0].durationMs
    : undefined,
  3000,
);
assert.equal(
  thinkingBlocks[0]?.kind === "run-thinking"
    ? thinkingBlocks[0].active
    : true,
  false,
);
assert.equal(
  thinkingBlocks[1]?.kind === "run-thinking"
    ? thinkingBlocks[1].thinking
    : "",
  "three",
);
assert.equal(
  thinkingBlocks[1]?.kind === "run-thinking"
    ? thinkingBlocks[1].active
    : false,
  true,
);
assert.equal(
  thinkingBlocks[1]?.kind === "run-thinking"
    ? thinkingBlocks[1].durationMs
    : undefined,
  undefined,
);
assert.deepEqual(
  mergedBlocks.map((block) => block.kind),
  [
    "entry",
    "run-thinking",
    "tools",
    "entry",
    "entry",
    "run-thinking",
  ],
);

const categorizedTools = groupTimelineEntries([
  {
    kind: "message",
    message: {
      id: "category-user",
      role: "user",
      content: "inspect and update",
      streaming: false,
    },
  },
  {
    kind: "tool",
    tool: {
      id: "read-a",
      name: "read_file",
      action: "Read",
      target: "src/a.ts",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "message",
    message: {
      id: "category-mid",
      role: "assistant",
      content: "I found the relevant code.",
      streaming: false,
    },
  },
  {
    kind: "tool",
    tool: {
      id: "search",
      name: "grep",
      action: "Read",
      target: "auth",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "read-a-again",
      name: "read_file",
      action: "Read",
      target: "src/a.ts",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "read-b",
      name: "read_file",
      action: "Read",
      target: "src/b.ts",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "browser-failed",
      name: "browser_bridge",
      action: "Browser",
      target: "click 2",
      status: "Failed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "browser-complete",
      name: "browser_bridge",
      action: "Browser",
      target: "read_state page_1",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "active-read",
      name: "read_file",
      action: "Read",
      target: "src/c.ts",
      status: "Running",
      details: [],
    },
  },
] as TimelineEntry[]);

const exploreGroup = categorizedTools.find(
  (block) => block.kind === "tool-group" &&
    block.category === "explore",
);
assert.deepEqual(
  categorizedTools.map((block) => block.kind),
  [
    "entry",
    "tools",
    "entry",
    "tool-group",
    "tool-group",
    "tools",
  ],
);
assert.equal(
  exploreGroup?.kind === "tool-group"
    ? exploreGroup.summary
    : undefined,
  "读取 2 个文件 · 检索 1 次",
);
assert.equal(
  exploreGroup?.kind === "tool-group"
    ? exploreGroup.tools.length
    : undefined,
  3,
);
const browserGroup = categorizedTools.find(
  (block) => block.kind === "tool-group" &&
    block.category === "browser",
);
assert.equal(
  browserGroup?.kind === "tool-group"
    ? browserGroup.failedCount
    : undefined,
  1,
);
const plainToolBlocks = categorizedTools.filter(
  (block) => block.kind === "tools",
);
assert.deepEqual(
  plainToolBlocks.map((block) =>
    block.kind === "tools"
      ? block.tools.map((tool) => tool.id)
      : [],
  ),
  [["read-a"], ["active-read"]],
);

const editGroupBlocks = groupTimelineEntries([
  {
    kind: "tool",
    runId: "edit-run",
    tool: {
      id: "edit-a",
      name: "edit_file",
      action: "Edit",
      target: "src/a.ts",
      status: "Completed",
      details: [],
      detail: {
        kind: "diff",
        path: "src/a.ts",
        rows: [],
        added: 3,
        removed: 1,
      },
    },
  },
  {
    kind: "tool",
    runId: "edit-run",
    tool: {
      id: "edit-b",
      name: "write_file",
      action: "Edit",
      target: "src/b.ts",
      status: "Completed",
      details: [],
      detail: {
        kind: "diff",
        path: "src/b.ts",
        rows: [],
        added: 2,
        removed: 1,
      },
    },
  },
] as TimelineEntry[], [
  {
    id: "edit-run",
    status: "completed",
    startedAt: 1,
    completedAt: 2,
  },
]);
assert.equal(
  editGroupBlocks[0]?.kind === "tool-group"
    ? editGroupBlocks[0].summary
    : undefined,
  "修改 2 个文件 · +5 −2",
);

const webGroupBlocks = groupTimelineEntries([
  {
    kind: "tool",
    runId: "web-run",
    tool: {
      id: "web-search",
      name: "web_search",
      action: "Web",
      target: "DeepSeek docs",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    runId: "web-run",
    tool: {
      id: "web-fetch",
      name: "web_fetch",
      action: "Web",
      target: "https://api-docs.deepseek.com/",
      status: "Completed",
      details: [],
    },
  },
] as TimelineEntry[], [
  {
    id: "web-run",
    status: "completed",
    startedAt: 1,
    completedAt: 2,
  },
]);
assert.equal(
  webGroupBlocks[0]?.kind === "tool-group"
    ? webGroupBlocks[0].summary
    : undefined,
  "搜索 1 次 · 抓取 1 页",
);

const memoryGroupBlocks = groupTimelineEntries([
  {
    kind: "tool",
    runId: "memory-run",
    tool: {
      id: "memory-list",
      name: "memory_list",
      action: "Memory",
      target: "memory inventory",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    runId: "memory-run",
    tool: {
      id: "memory-write",
      name: "memory_write",
      action: "Memory",
      target: "workspace:testing",
      status: "Completed",
      details: [],
    },
  },
] as TimelineEntry[], [
  {
    id: "memory-run",
    status: "completed",
    startedAt: 1,
    completedAt: 2,
  },
]);
assert.equal(
  memoryGroupBlocks[0]?.kind === "tool-group"
    ? memoryGroupBlocks[0].category
    : undefined,
  "memory",
);
assert.equal(
  memoryGroupBlocks[0]?.kind === "tool-group"
    ? memoryGroupBlocks[0].summary
    : undefined,
  "查看 1 次 · 更新 1 次",
);

const splitSegments = groupTimelineEntries([
  {
    kind: "tool",
    tool: {
      id: "search-a",
      name: "grep",
      action: "Read",
      target: "alpha",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "search-b",
      name: "grep",
      action: "Read",
      target: "beta",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "command-a",
      name: "run_command",
      action: "Run",
      target: "npm test",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "search-c",
      name: "grep",
      action: "Read",
      target: "gamma",
      status: "Completed",
      details: [],
    },
  },
] as TimelineEntry[]);
assert.deepEqual(
  splitSegments.map((block) => block.kind),
  ["tool-group", "tools"],
);
assert.deepEqual(
  splitSegments[0]?.kind === "tool-group"
    ? splitSegments[0].tools.map((tool) => tool.id)
    : undefined,
  ["search-a", "search-b"],
);
assert.deepEqual(
  splitSegments[1]?.kind === "tools"
    ? splitSegments[1].tools.map((tool) => tool.id)
    : undefined,
  ["command-a", "search-c"],
);

const activeSameCategory = groupTimelineEntries([
  {
    kind: "tool",
    tool: {
      id: "active-search-a",
      name: "grep",
      action: "Read",
      target: "alpha",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "active-search-b",
      name: "grep",
      action: "Read",
      target: "beta",
      status: "Completed",
      details: [],
    },
  },
] as TimelineEntry[]);
assert.deepEqual(
  activeSameCategory.map((block) => block.kind),
  ["tools"],
);

const questionBlocks = groupTimelineEntries([
  {
    kind: "tool",
    tool: {
      id: "question-a",
      name: "ask_user",
      action: "Run",
      target: "Choose A",
      status: "Completed",
      details: [],
    },
  },
  {
    kind: "tool",
    tool: {
      id: "question-b",
      name: "ask_user",
      action: "Run",
      target: "Choose B",
      status: "Completed",
      details: [],
    },
  },
] as TimelineEntry[]);
assert.deepEqual(
  questionBlocks.map((block) => block.kind),
  ["tools"],
);

const completedRunBlocks = groupTimelineEntries(
  [
    {
      kind: "message",
      runId: "run-complete",
      message: {
        id: "user-complete",
        role: "user",
        content: "update it",
        streaming: false,
      },
    },
    {
      kind: "message",
      runId: "run-complete",
      message: {
        id: "assistant-complete",
        role: "assistant",
        content: "",
        thinking: "inspect the code",
        thinkingDone: true,
        thinkingDurationMs: 1200,
        streaming: false,
      },
    },
    {
      kind: "tool",
      runId: "run-complete",
      tool: {
        id: "tool-complete",
        action: "Edit",
        target: "a.py",
        status: "Completed",
        details: [],
      },
    },
    {
      kind: "message",
      runId: "run-complete",
      message: {
        id: "answer-complete",
        role: "assistant",
        content: "Updated the file.",
        streaming: false,
      },
    },
  ] as TimelineEntry[],
  [
    {
      id: "run-complete",
      status: "completed",
      startedAt: 1_000,
      completedAt: 3_400,
    },
  ],
);
assert.deepEqual(
  completedRunBlocks.map((block) => block.kind),
  ["entry", "run-activity", "entry"],
);
assert.equal(
  completedRunBlocks[2]?.kind === "entry"
    ? completedRunBlocks[2].copyText
    : undefined,
  "[Edit] a.py — Completed\n\nUpdated the file.",
);
assert.equal(
  completedRunBlocks[1]?.kind === "run-activity"
    ? completedRunBlocks[1].durationMs
    : undefined,
  2_400,
);
assert.equal(
  completedRunBlocks[2]?.kind === "entry"
    ? completedRunBlocks[2].entry.message.content
    : undefined,
  "Updated the file.",
);
assert.equal(formatRunDuration(13_400), "13秒");
assert.equal(formatRunDuration(62_000), "1分钟 2秒");
assert.equal(formatRunDuration(60_000), "1分钟");

const runningRunBlocks = groupTimelineEntries(
  [
    {
      kind: "message",
      runId: "run-running",
      message: {
        id: "user-running",
        role: "user",
        content: "update it",
        streaming: false,
      },
    },
    {
      kind: "tool",
      runId: "run-running",
      tool: {
        id: "tool-running",
        action: "Read",
        target: "a.py",
        status: "Running",
        details: [],
      },
    },
    {
      kind: "message",
      runId: "run-running",
      message: {
        id: "answer-running",
        role: "assistant",
        content: "Still working.",
        streaming: true,
      },
    },
  ] as TimelineEntry[],
  [{ id: "run-running", status: "running", startedAt: 1_000 }],
);
assert.ok(
  runningRunBlocks.every(
    (block) => block.kind !== "run-activity",
  ),
);

const changedRunBlocks = groupTimelineEntries(
  [
    {
      kind: "message",
      runId: "run-changed",
      message: {
        id: "user-changed",
        role: "user",
        content: "change two files",
        streaming: false,
      },
    },
    {
      kind: "tool",
      runId: "run-changed",
      tool: {
        id: "tool-changed",
        action: "Edit",
        target: "a.py",
        status: "Completed",
        details: [],
      },
    },
    {
      kind: "message",
      runId: "run-changed",
      message: {
        id: "answer-changed",
        role: "assistant",
        content: "Updated both.",
        streaming: false,
      },
    },
  ] as TimelineEntry[],
  [
    {
      id: "run-changed",
      status: "completed",
      startedAt: 1_000,
      completedAt: 2_000,
      changes: [
        { path: "a.py", added: 2, removed: 1 },
        { path: "b.py", added: 3, removed: 0 },
      ],
      changesReverted: false,
    },
  ],
);
const changesBlock = changedRunBlocks.find(
  (block) => block.kind === "changes",
);
assert.equal(changesBlock?.kind, "changes");
assert.deepEqual(
  changesBlock?.kind === "changes" ? changesBlock.changes : [],
  [
    { path: "a.py", added: 2, removed: 1 },
    { path: "b.py", added: 3, removed: 0 },
  ],
);

console.log("timelineReducer and transport tests passed");

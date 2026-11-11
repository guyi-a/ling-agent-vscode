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
import type { TimelineState } from "../webview/src/types";
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
  relativePath: ".ling/attachments/pasted-ab12.png",
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
      relativePath: ".ling/attachments/doc-ab12.pdf",
      name: "doc.pdf",
      mediaType: "application/pdf",
      kind: "file",
    },
    {
      relativePath: ".ling/attachments/pasted-ab12.png",
      name: "pasted-ab12.png",
      mediaType: "image/png",
      kind: "image",
    },
  ]),
  [
    {
      relativePath: ".ling/attachments/doc-ab12.pdf",
      name: "doc.pdf",
      mediaType: "application/pdf",
      kind: "file",
    },
    {
      relativePath: ".ling/attachments/pasted-ab12.png",
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
        relativePath: ".ling/attachments/doc-ab12.pdf",
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
        relativePath: ".ling/attachments/doc-ab12.pdf",
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
    relativePath: ".ling/attachments/pasted-ab12.png",
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

console.log("timelineReducer and transport tests passed");

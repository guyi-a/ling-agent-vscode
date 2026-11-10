import type { UiEvent } from "./uiProtocol";
import type { ApprovalRequest, ToolCall } from "./uiTypes";

export interface ScheduledEvent {
  delayMs: number;
  event: UiEvent;
}

const READ_TOOL: ToolCall = {
  id: "tool-read-session",
  action: "Read",
  target: "src/session.ts",
  status: "Running",
  details: ["Tool: read_file", "Path: src/session.ts", "Scope: workspace"],
};

export const APPROVAL_EDIT: ApprovalRequest = {
  id: "approval-edit-session",
  tool: "edit_file",
  action: "Edit",
  target: "src/session.ts",
  reason: "This change updates application code.",
  effectDigest: "demo-edit-digest",
  details: [
    "Tool: edit_file",
    "Path: src/session.ts",
    "Effect: filesystem write",
    "Scope: workspace",
  ],
};

export const APPROVAL_RUN: ApprovalRequest = {
  id: "approval-run-tests",
  tool: "run_command",
  action: "Run",
  target: "npm test",
  reason: "This command needs approval in Manual mode.",
  effectDigest: "demo-run-digest",
  details: [
    "Tool: run_command",
    "Command: npm test",
    "Classification: normal",
  ],
};

export const MOCK_EVENTS: ScheduledEvent[] = [
  {
    delayMs: 500,
    event: {
      type: "session.snapshot",
      snapshot: {
        phase: "running",
        entries: [
          {
            kind: "message",
            message: {
              id: "message-user-1",
              role: "user",
              content: "Tighten the empty state without losing the composer.",
              streaming: false,
            },
          },
          {
            kind: "message",
            message: {
              id: "message-assistant-1",
              role: "assistant",
              content: "",
              streaming: true,
            },
          },
          {
            kind: "tool",
            tool: READ_TOOL,
          },
          {
            kind: "tool",
            tool: {
              id: "tool-edit-session",
              action: "Edit",
              target: "src/session.ts",
              status: "Waiting",
              details: [
                "Tool: edit_file",
                "Path: src/session.ts",
                "Effect: filesystem write",
              ],
            },
          },
          {
            kind: "tool",
            tool: {
              id: "tool-run-tests",
              action: "Run",
              target: "npm test",
              status: "Waiting",
              details: [
                "Tool: run_command",
                "Command: npm test",
                "Classification: normal",
              ],
            },
          },
        ],
        pendingApprovals: [],
      },
    },
  },
  {
    delayMs: 260,
    event: {
      type: "message.delta",
      messageId: "message-assistant-1",
      role: "assistant",
      delta: "### Current structure\n\n",
    },
  },
  {
    delayMs: 520,
    event: {
      type: "message.delta",
      messageId: "message-assistant-1",
      role: "assistant",
      delta:
        "The request moves through three layers:\n\n" +
        "| Layer | Role |\n" +
        "|---|---|\n" +
        "| Extension Host | Manages the sidecar |\n" +
        "| Sidecar | Runs the agent |\n" +
        "| Webview | Renders the timeline |\n\n" +
        "```text\nUI -> Host -> Sidecar\n```",
    },
  },
  {
    delayMs: 760,
    event: {
      type: "message.completed",
      messageId: "message-assistant-1",
    },
  },
  {
    delayMs: 900,
    event: {
      type: "tool.updated",
      tool: {
        ...READ_TOOL,
        status: "Completed",
      },
    },
  },
  {
    delayMs: 1080,
    event: {
      type: "approval.requested",
      request: APPROVAL_EDIT,
    },
  },
  {
    delayMs: 1200,
    event: {
      type: "approval.requested",
      request: APPROVAL_RUN,
    },
  },
];

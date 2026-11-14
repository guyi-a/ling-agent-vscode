import type { UiAction } from "../protocol";
import type {
  ApprovalResult,
  BackgroundTaskView,
  QuestionRequest,
  RunPhase,
  SessionSnapshot,
  SubagentTraceEntry,
  TimelineEntry,
  TimelineMessage,
  TimelineState,
  ToolCall,
} from "../types";

export function createEmptyTimelineState(): TimelineState {
  return {
    phase: "idle",
    entries: [],
    pendingApprovals: [],
    pendingQuestions: [],
    queuedMessages: [],
    runs: [],
    error: null,
    readOnly: false,
    subagentTraces: {},
    backgroundTasks: [],
    computerSession: null,
  };
}

export function timelineReducer(
  state: TimelineState,
  action: UiAction,
): TimelineState {
  switch (action.type) {
    case "session.reset":
      return createEmptyTimelineState();

    case "session.snapshot":
      return fromSnapshot(
        action.snapshot,
        state.subagentTraces,
      );

    case "message.delta":
      return appendMessageDelta(state, action.messageId, action.delta);

    case "thinking.delta":
      return appendThinkingDelta(state, action.messageId, action.delta);

    case "thinking.completed":
      return completeThinking(state, action.messageId);

    case "message.completed":
      return completeMessage(state, action.messageId);

    case "tool.updated":
      return upsertTool(state, action.tool);

    case "subagent.trace":
      return appendSubagentTrace(
        state,
        action.callId,
        action.entry,
      );

    case "plan.created":
      // The extension handles this event; it is not part of the timeline.
      return state;

    case "approval.requested":
      if (state.phase === "cancelled" || state.phase === "failed") {
        return state;
      }
      return enqueueApproval(state, action.request);

    case "approval.resolved":
      return resolveApproval(state, action.approvalId, action.approved);

    case "question.requested":
      if (state.phase === "cancelled" || state.phase === "failed") {
        return state;
      }
      return enqueueQuestion(state, action.question);

    case "question.resolved":
      return resolveQuestion(state, action.callId);

    case "session.queue":
      return {
        ...state,
        queuedMessages: action.queuedMessages.map((message) => ({
          ...message,
        })),
        error: null,
      };

    case "background.updated":
      return {
        ...state,
        backgroundTasks: upsertBackgroundTask(
          state.backgroundTasks,
          action.task,
        ),
      };

    case "computer.session.updated":
      return {
        ...state,
        computerSession: action.session,
      };

    case "run.cancelled":
      return cancelRun(state);

    case "run.failed":
      return failRun(state, action.message);
  }
}

function fromSnapshot(
  snapshot: SessionSnapshot,
  previousTraces: Record<string, SubagentTraceEntry[]> = {},
): TimelineState {
  const subagentTraces: Record<string, SubagentTraceEntry[]> = {};
  for (const entry of snapshot.entries) {
    if (entry.kind !== "tool") {
      continue;
    }
    if (
      entry.tool.detail?.kind === "subagent" &&
      entry.tool.detail.trace
    ) {
      subagentTraces[entry.tool.id] = entry.tool.detail.trace.map(
        (traceEntry) => ({ ...traceEntry }),
      );
    } else if (previousTraces[entry.tool.id]) {
      subagentTraces[entry.tool.id] =
        previousTraces[entry.tool.id];
    }
  }
  return {
    phase: snapshot.phase,
    entries: snapshot.entries.map(cloneEntry),
    subagentTraces,
    pendingApprovals: snapshot.pendingApprovals.map((request) => ({
      ...request,
      details: [...request.details],
    })),
    pendingQuestions: (snapshot.pendingQuestions ?? []).map(
      cloneQuestionRequest,
    ),
    queuedMessages: (snapshot.queuedMessages ?? []).map((message) => ({
      ...message,
    })),
    runs: (snapshot.runs ?? []).map((run) => ({ ...run })),
    backgroundTasks: (snapshot.backgroundTasks ?? []).map((task) => ({
      ...task,
    })),
    computerSession: snapshot.computerSession ?? null,
    error: snapshot.error ?? null,
    sessionId: snapshot.sessionId,
    readOnly: snapshot.readOnly ?? false,
  };
}

function upsertBackgroundTask(
  tasks: BackgroundTaskView[],
  task: BackgroundTaskView,
): BackgroundTaskView[] {
  const index = tasks.findIndex((item) => item.id === task.id);
  if (index < 0) {
    return [...tasks, { ...task }];
  }
  const next = [...tasks];
  next[index] = { ...task };
  return next;
}

function cloneEntry(entry: TimelineEntry): TimelineEntry {
  if (entry.kind === "message") {
    return {
      ...entry,
      kind: "message",
      message: {
        ...entry.message,
        ...(entry.message.attachments
          ? {
              attachments: entry.message.attachments.map(
                (attachment) => ({ ...attachment }),
              ),
            }
          : {}),
        ...(entry.message.selections
          ? {
              selections: entry.message.selections.map(
                (selection) => ({ ...selection }),
              ),
            }
          : {}),
      },
    };
  }

  if (entry.kind === "tool") {
    return {
      ...entry,
      kind: "tool",
      tool: {
        ...entry.tool,
        details: [...entry.tool.details],
      },
    };
  }

  if (entry.kind === "compaction") {
    return {
      ...entry,
      kind: "compaction",
      compaction: { ...entry.compaction },
    };
  }

  if (entry.kind === "task-notification") {
    return {
      ...entry,
      kind: "task-notification",
      tasks: entry.tasks.map((task) => ({ ...task })),
    };
  }

  return {
    ...entry,
    kind: "approval",
    approval: { ...entry.approval },
  };
}

function appendMessageDelta(
  state: TimelineState,
  messageId: string,
  delta: string,
): TimelineState {
  let found = false;
  const entries = state.entries.map((entry) => {
    if (
      entry.kind !== "message" ||
      entry.message.id !== messageId ||
      entry.message.role !== "assistant"
    ) {
      return entry;
    }

    found = true;
    const thinkingPatch = entry.message.thinking
      ? {
          thinkingDone: true,
          ...(entry.message.thinkingStartedAt !== undefined &&
          entry.message.thinkingDurationMs === undefined
            ? {
                thinkingDurationMs:
                  Date.now() - entry.message.thinkingStartedAt,
              }
            : {}),
        }
      : {};
    return {
      ...entry,
      kind: "message" as const,
      message: {
        ...entry.message,
        content: entry.message.content + delta,
        ...thinkingPatch,
        streaming: true,
      },
    };
  });

  if (!found) {
    entries.push({
      kind: "message",
      ...(activeRunId(state)
        ? { runId: activeRunId(state) }
        : {}),
      message: {
        id: messageId,
        role: "assistant",
        content: delta,
        streaming: true,
      },
    });
  }

  return {
    ...state,
    phase: state.phase === "idle" ? "running" : state.phase,
    entries,
    error: null,
  };
}

function appendThinkingDelta(
  state: TimelineState,
  messageId: string,
  delta: string,
): TimelineState {
  let found = false;
  const entries = state.entries.map((entry) => {
    if (
      entry.kind !== "message" ||
      entry.message.id !== messageId ||
      entry.message.role !== "assistant"
    ) {
      return entry;
    }

    found = true;
    const continuing =
      entry.message.thinkingStartedAt !== undefined &&
      !entry.message.thinkingDone &&
      entry.message.thinkingDurationMs === undefined;
    return {
      ...entry,
      kind: "message" as const,
      message: {
        ...entry.message,
        thinking: (entry.message.thinking ?? "") + delta,
        thinkingDone: false,
        thinkingStartedAt: continuing
          ? entry.message.thinkingStartedAt
          : Date.now(),
        thinkingDurationMs: undefined,
        streaming: true,
      },
    };
  });

  if (!found) {
    entries.push({
      kind: "message",
      ...(activeRunId(state)
        ? { runId: activeRunId(state) }
        : {}),
      message: {
        id: messageId,
        role: "assistant",
        content: "",
        thinking: delta,
        thinkingDone: false,
        thinkingStartedAt: Date.now(),
        streaming: true,
      },
    });
  }

  return {
    ...state,
    phase: state.phase === "idle" ? "running" : state.phase,
    entries,
    error: null,
  };
}

function completeThinking(
  state: TimelineState,
  messageId: string,
): TimelineState {
  return {
    ...state,
    entries: state.entries.map((entry) => {
      if (
        entry.kind !== "message" ||
        entry.message.id !== messageId ||
        entry.message.role !== "assistant"
      ) {
        return entry;
      }
      const duration =
        entry.message.thinkingDurationMs ??
        (entry.message.thinkingStartedAt !== undefined
          ? Date.now() - entry.message.thinkingStartedAt
          : undefined);
      return {
        ...entry,
        kind: "message",
        message: {
          ...entry.message,
          thinkingDone: true,
          ...(duration !== undefined
            ? { thinkingDurationMs: duration }
            : {}),
        },
      };
    }),
  };
}

function completeMessage(
  state: TimelineState,
  messageId: string,
): TimelineState {
  return {
    ...state,
    phase:
      state.pendingApprovals.length > 0
        ? "waiting_approval"
        : nextPhaseAfterActivity(state.entries),
    entries: state.entries.map((entry) =>
      entry.kind === "message" && entry.message.id === messageId
        ? {
            ...entry,
            kind: "message",
            message: {
              ...entry.message,
              ...(entry.message.thinking
                ? { thinkingDone: true }
                : {}),
              streaming: false,
            },
          }
        : entry,
    ),
  };
}

function appendSubagentTrace(
  state: TimelineState,
  callId: string,
  entry: SubagentTraceEntry,
): TimelineState {
  const current = state.subagentTraces[callId] ?? [];
  return {
    ...state,
    subagentTraces: {
      ...state.subagentTraces,
      [callId]: [...current, { ...entry }],
    },
  };
}

function upsertTool(state: TimelineState, tool: ToolCall): TimelineState {
  let found = false;
  const entries = state.entries.map((entry) => {
    if (entry.kind !== "tool" || entry.tool.id !== tool.id) {
      return entry;
    }

    found = true;
    return {
      ...entry,
      kind: "tool" as const,
      tool: {
        ...tool,
        details: [...tool.details],
      },
    };
  });

  if (!found) {
    entries.push({
      kind: "tool",
      ...(activeRunId(state)
        ? { runId: activeRunId(state) }
        : {}),
      tool: {
        ...tool,
        details: [...tool.details],
      },
    });
  }

  return {
    ...state,
    phase:
      state.phase === "idle" &&
      (tool.status === "Running" || tool.status === "Waiting")
        ? "running"
        : state.phase,
    entries,
    error: null,
  };
}

function enqueueApproval(
  state: TimelineState,
  request: TimelineState["pendingApprovals"][number],
): TimelineState {
  if (state.pendingApprovals.some((item) => item.id === request.id)) {
    return state;
  }

  return {
    ...state,
    phase: "waiting_approval",
    pendingApprovals: [
      ...state.pendingApprovals,
      {
        ...request,
        details: [...request.details],
      },
    ],
    error: null,
  };
}

function cloneQuestionRequest(request: QuestionRequest): QuestionRequest {
  return {
    callId: request.callId,
    questions: request.questions.map((question) => ({
      ...question,
      options: question.options.map((option) => ({ ...option })),
    })),
  };
}

function enqueueQuestion(
  state: TimelineState,
  request: QuestionRequest,
): TimelineState {
  const pendingQuestions = state.pendingQuestions ?? [];
  if (pendingQuestions.some((item) => item.callId === request.callId)) {
    return state;
  }

  return {
    ...state,
    pendingQuestions: [...pendingQuestions, cloneQuestionRequest(request)],
    error: null,
  };
}

function resolveQuestion(
  state: TimelineState,
  callId: string,
): TimelineState {
  const pendingQuestions = state.pendingQuestions ?? [];
  if (!pendingQuestions.some((item) => item.callId === callId)) {
    return state;
  }
  return {
    ...state,
    pendingQuestions: pendingQuestions.filter(
      (item) => item.callId !== callId,
    ),
    error: null,
  };
}

function resolveApproval(
  state: TimelineState,
  approvalId: string,
  approved: boolean,
): TimelineState {
  const request = state.pendingApprovals.find(
    (item) => item.id === approvalId,
  );
  if (!request) {
    return state;
  }

  const result: ApprovalResult = {
    id: `result-${request.id}`,
    approvalId: request.id,
    approved,
    action: request.action,
    target: request.target,
  };
  const pendingApprovals = state.pendingApprovals.filter(
    (item) => item.id !== approvalId,
  );

  return {
    ...state,
    phase: pendingApprovals.length > 0 ? "waiting_approval" : "running",
    entries: [
      ...state.entries,
      {
        kind: "approval",
        approval: result,
      },
    ],
    pendingApprovals,
    error: null,
  };
}

function cancelRun(state: TimelineState): TimelineState {
  return {
    ...state,
    phase: "cancelled",
    entries: state.entries.map((entry) =>
      entry.kind === "tool" &&
      (entry.tool.status === "Running" || entry.tool.status === "Waiting")
        ? {
            ...entry,
            kind: "tool",
            tool: {
              ...entry.tool,
              status: "Cancelled",
            },
          }
        : entry,
    ),
    pendingApprovals: [],
    pendingQuestions: [],
    queuedMessages: [],
    runs: finishRunningRuns(state.runs, "cancelled"),
    error: null,
  };
}

function failRun(state: TimelineState, message: string): TimelineState {
  return {
    ...state,
    phase: "failed",
    entries: state.entries.map((entry) =>
      entry.kind === "tool" &&
      (entry.tool.status === "Running" || entry.tool.status === "Waiting")
        ? {
            ...entry,
            kind: "tool",
            tool: {
              ...entry.tool,
              status: "Failed",
            },
          }
        : entry,
    ),
    pendingApprovals: [],
    pendingQuestions: [],
    queuedMessages: [],
    runs: finishRunningRuns(state.runs, "error"),
    error: message,
  };
}

function finishRunningRuns(
  runs: TimelineState["runs"],
  status: "cancelled" | "error",
): TimelineState["runs"] {
  const completedAt = Date.now();
  return (runs ?? []).map((run) =>
    run.status === "running"
      ? { ...run, status, completedAt }
      : { ...run },
  );
}

function activeRunId(state: TimelineState): string | undefined {
  return [...(state.runs ?? [])]
    .reverse()
    .find((run) => run.status === "running")?.id;
}

function nextPhaseAfterActivity(entries: TimelineEntry[]): RunPhase {
  const hasActiveTool = entries.some(
    (entry) =>
      entry.kind === "tool" &&
      (entry.tool.status === "Running" || entry.tool.status === "Waiting"),
  );
  return hasActiveTool ? "running" : "idle";
}

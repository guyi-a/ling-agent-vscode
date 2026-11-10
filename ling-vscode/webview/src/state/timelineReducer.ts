import type { UiAction } from "../protocol";
import type {
  ApprovalResult,
  QuestionRequest,
  RunPhase,
  SessionSnapshot,
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
    error: null,
    readOnly: false,
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
      return fromSnapshot(action.snapshot);

    case "message.delta":
      return appendMessageDelta(state, action.messageId, action.delta);

    case "message.completed":
      return completeMessage(state, action.messageId);

    case "tool.updated":
      return upsertTool(state, action.tool);

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

    case "run.cancelled":
      return cancelRun(state);

    case "run.failed":
      return failRun(state, action.message);
  }
}

function fromSnapshot(snapshot: SessionSnapshot): TimelineState {
  return {
    phase: snapshot.phase,
    entries: snapshot.entries.map(cloneEntry),
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
    error: snapshot.error ?? null,
    sessionId: snapshot.sessionId,
    readOnly: snapshot.readOnly ?? false,
  };
}

function cloneEntry(entry: TimelineEntry): TimelineEntry {
  if (entry.kind === "message") {
    return {
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
      kind: "tool",
      tool: {
        ...entry.tool,
        details: [...entry.tool.details],
      },
    };
  }

  if (entry.kind === "compaction") {
    return {
      kind: "compaction",
      compaction: { ...entry.compaction },
    };
  }

  return {
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
    return {
      kind: "message" as const,
      message: {
        ...entry.message,
        content: entry.message.content + delta,
        streaming: true,
      },
    };
  });

  if (!found) {
    entries.push({
      kind: "message",
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
            kind: "message",
            message: {
              ...entry.message,
              streaming: false,
            },
          }
        : entry,
    ),
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
    error: message,
  };
}

function nextPhaseAfterActivity(entries: TimelineEntry[]): RunPhase {
  const hasActiveTool = entries.some(
    (entry) =>
      entry.kind === "tool" &&
      (entry.tool.status === "Running" || entry.tool.status === "Waiting"),
  );
  return hasActiveTool ? "running" : "idle";
}

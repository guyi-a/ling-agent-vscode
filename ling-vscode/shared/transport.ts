import type { UiEvent } from "./uiProtocol";
import type {
  ApprovalMode,
  ApprovalRequest,
  AskUserAnswer,
  AskUserQuestion,
  DiffView,
  EffortLevel,
  HistorySession,
  IndexBuildStats,
  IndexStatus,
  MemoryScope,
  MemoryStatus,
  McpIssue,
  McpServerStatus,
  QueuedMessage,
  QuestionRequest,
  RunPhase,
  SessionSnapshot,
  SettingsView,
  SubagentTraceEntry,
  TimelineEntry,
  TimelineRunView,
  ToolAction,
  ToolCall,
  ToolDetail,
  ToolDiffRow,
  ToolStatus,
  UserAttachmentRef,
  UserAttachmentView,
  UserSelectionKind,
  UserSelectionRef,
  UserSelectionView,
  WorkspaceChangeSummary,
} from "./uiTypes";

export const UI_PROTOCOL_VERSION = 1;
export const MAX_ATTACHMENTS_PER_MESSAGE = 4;
export const MAX_SELECTIONS_PER_MESSAGE = 4;

export type WebviewToHostMessage =
  | {
      type: "ui.ready";
      host: "browser" | "vscode";
      protocol: number;
    }
  | {
      type: "session.new";
    }
  | {
      type: "chat.submit";
      text: string;
      approvalMode: ApprovalMode;
      effort?: EffortLevel;
      attachments?: UserAttachmentRef[];
      selections?: UserSelectionRef[];
    }
  | {
      type: "attachment.save";
      requestId: string;
      name: string;
      mimeType: string;
      dataBase64: string;
    }
  | {
      type: "attachment.pick";
    }
  | {
      type: "attachment.open";
      relativePath: string;
    }
  | {
      type: "selection.capture";
      source: "any" | "editor" | "terminal";
    }
  | {
      type: "selection.copy";
      text: string;
    }
  | {
      type: "selection.open";
      path: string;
      startLine?: number;
      endLine?: number;
    }
  | {
      type: "mode.change";
      mode: ApprovalMode;
    }
  | {
      type: "settings.get";
    }
  | {
      type: "settings.save";
      apiKey: string;
      model: string;
      baseUrl: string;
      effort: EffortLevel;
      diffView: DiffView;
      maxSteps: number;
      maxParallelTools: number;
      commandTimeoutSeconds: number;
      approvalTimeoutSeconds: number;
      contextWindowTokens: number;
      keepLastUserTurns: number;
      embeddingModel: string;
      embeddingBaseUrl: string;
      embeddingApiKey: string;
    }
  | {
      type: "effort.change";
      effort: EffortLevel;
    }
  | {
      type: "approval.resolve";
      approvalId: string;
      effectDigest: string;
      approved: boolean;
    }
  | {
      type: "question.answer";
      callId: string;
      answers?: AskUserAnswer[];
      cancelled?: boolean;
    }
  | {
      type: "session.queued.sendNow";
      queuedId: string;
    }
  | {
      type: "run.cancel";
    }
  | {
      type: "message.delete";
      messageId: string;
    }
  | {
      type: "message.regenerate";
      messageId: string;
      effort?: EffortLevel;
    }
  | {
      type: "changes.open";
      sessionId: string;
      runId: string;
    }
  | {
      type: "changes.revert";
      sessionId: string;
      runId: string;
    }
  | {
      type: "history.list";
    }
  | {
      type: "history.search";
      query: string;
    }
  | {
      type: "history.delete";
      sessionId: string;
    }
  | {
      type: "history.open";
      sessionId: string;
    }
  | {
      type: "index.build";
    }
  | {
      type: "plan.open";
      path: string;
    }
  | {
      type: "plan.build";
      path: string;
    }
  | {
      type: "memory.open";
      path: string;
    }
  | {
      type: "memory.create";
      scope: MemoryScope;
    }
  | {
      type: "memory.delete";
      name: string;
      scope: MemoryScope;
    }
  | {
      type: "mcp.openConfig";
    }
  | {
      type: "mcp.authorize";
      name: string;
    }
  | {
      type: "mcp.revokeAuth";
      name: string;
    };

export type HostToWebviewMessage =
  | {
      type: "ling.ui-event";
      sequence: number;
      event: UiEvent;
    }
  | {
      type: "ling.history-list";
      sessions: HistorySession[];
      error?: string;
    }
  | {
      type: "ling.host-context";
      workspaceName: string;
    }
  | {
      type: "ling.settings";
      settings: SettingsView;
      error?: string;
    }
  | {
      type: "ling.settings-saved";
      settings?: SettingsView;
      error?: string;
      warning?: string;
    }
  | {
      type: "ling.attachment-saved";
      requestId: string;
      attachment?: UserAttachmentView;
      error?: string;
    }
  | {
      type: "ling.selection-added";
      selection?: UserSelectionView;
      error?: string;
    }
  | {
      type: "ling.index-result";
      ok: boolean;
      stats?: IndexBuildStats;
      error?: string;
    }
  | {
      type: "ling.index-status";
      status: IndexStatus;
    }
  | {
      type: "ling.mode-changed";
      mode: ApprovalMode;
    }
  | {
      type: "ling.ready";
    };

export function isWebviewToHostMessage(
  value: unknown,
): value is WebviewToHostMessage {
  if (!isRecord(value) || typeof value.type !== "string") {
    return false;
  }

  switch (value.type) {
    case "ui.ready":
      return (
        (value.host === "browser" || value.host === "vscode") &&
        typeof value.protocol === "number"
      );
    case "session.new":
    case "history.list":
      return true;
    case "run.cancel":
      return true;
    case "message.delete":
      return (
        typeof value.messageId === "string" &&
        value.messageId.length > 0
      );
    case "message.regenerate":
      return (
        typeof value.messageId === "string" &&
        value.messageId.length > 0 &&
        (value.effort === undefined || isEffortLevel(value.effort))
      );
    case "changes.open":
    case "changes.revert":
      return (
        typeof value.sessionId === "string" &&
        value.sessionId.length > 0 &&
        typeof value.runId === "string" &&
        value.runId.length > 0
      );
    case "history.search":
      return typeof value.query === "string";
    case "history.delete":
    case "history.open":
      return (
        typeof value.sessionId === "string" &&
        value.sessionId.length > 0
      );
    case "chat.submit":
      return (
        typeof value.text === "string" &&
        isApprovalMode(value.approvalMode) &&
        (value.effort === undefined || isEffortLevel(value.effort)) &&
        (value.attachments === undefined ||
          (Array.isArray(value.attachments) &&
            value.attachments.length <= MAX_ATTACHMENTS_PER_MESSAGE &&
            value.attachments.every(isUserAttachmentRef))) &&
        (value.selections === undefined ||
          (Array.isArray(value.selections) &&
            value.selections.length <= MAX_SELECTIONS_PER_MESSAGE &&
            value.selections.every(isUserSelectionRef)))
      );
    case "attachment.save":
      return (
        typeof value.requestId === "string" &&
        value.requestId.length > 0 &&
        typeof value.name === "string" &&
        typeof value.mimeType === "string" &&
        typeof value.dataBase64 === "string" &&
        value.dataBase64.length > 0
      );
    case "attachment.pick":
      return true;
    case "attachment.open":
      return (
        typeof value.relativePath === "string" &&
        value.relativePath.length > 0
      );
    case "selection.capture":
      return (
        value.source === "any" ||
        value.source === "editor" ||
        value.source === "terminal"
      );
    case "selection.copy":
      return typeof value.text === "string" && value.text.length > 0;
    case "selection.open":
      return (
        typeof value.path === "string" &&
        value.path.length > 0 &&
        (value.startLine === undefined ||
          (typeof value.startLine === "number" &&
            Number.isInteger(value.startLine) &&
            value.startLine >= 1)) &&
        (value.endLine === undefined ||
          (typeof value.endLine === "number" &&
            Number.isInteger(value.endLine) &&
            value.endLine >= 1))
      );
    case "mode.change":
      return isApprovalMode(value.mode);
    case "settings.get":
      return true;
    case "index.build":
      return true;
    case "plan.open":
    case "plan.build":
      return typeof value.path === "string" && value.path.length > 0;
    case "memory.open":
      return typeof value.path === "string" && value.path.length > 0;
    case "memory.create":
      return value.scope === "workspace" || value.scope === "global";
    case "memory.delete":
      return (
        typeof value.name === "string" &&
        value.name.length > 0 &&
        (value.scope === "workspace" || value.scope === "global")
      );
    case "mcp.openConfig":
      return true;
    case "mcp.authorize":
    case "mcp.revokeAuth":
      return typeof value.name === "string" && value.name.length > 0;
    case "settings.save":
      return (
        typeof value.apiKey === "string" &&
        typeof value.model === "string" &&
        typeof value.baseUrl === "string" &&
        isEffortLevel(value.effort) &&
        isDiffView(value.diffView) &&
        isPositiveInteger(value.maxSteps) &&
        isPositiveInteger(value.maxParallelTools) &&
        isPositiveNumber(value.commandTimeoutSeconds) &&
        isPositiveNumber(value.approvalTimeoutSeconds) &&
        isPositiveInteger(value.contextWindowTokens) &&
        isNonNegativeInteger(value.keepLastUserTurns) &&
        typeof value.embeddingModel === "string" &&
        typeof value.embeddingBaseUrl === "string" &&
        typeof value.embeddingApiKey === "string"
      );
    case "effort.change":
      return isEffortLevel(value.effort);
    case "approval.resolve":
      return (
        typeof value.approvalId === "string" &&
        typeof value.effectDigest === "string" &&
        typeof value.approved === "boolean"
      );
    case "question.answer":
      return (
        typeof value.callId === "string" &&
        value.callId.length > 0 &&
        (value.cancelled === undefined ||
          typeof value.cancelled === "boolean") &&
        (value.cancelled === true ||
          (Array.isArray(value.answers) &&
            value.answers.every(isAskUserAnswer)))
      );
    case "session.queued.sendNow":
      return (
        typeof value.queuedId === "string" &&
        value.queuedId.length > 0
      );
    default:
      return false;
  }
}

export function isHostToWebviewMessage(
  value: unknown,
): value is HostToWebviewMessage {
  if (!isRecord(value)) {
    return false;
  }
  if (value.type === "ling.history-list") {
    return (
      Array.isArray(value.sessions) &&
      value.sessions.every(isHistorySession) &&
      (value.error === undefined ||
        typeof value.error === "string")
    );
  }
  if (value.type === "ling.host-context") {
    return typeof value.workspaceName === "string";
  }
  if (value.type === "ling.settings") {
    return (
      isSettingsView(value.settings) &&
      (value.error === undefined ||
        typeof value.error === "string")
    );
  }
  if (value.type === "ling.settings-saved") {
    return (
      (value.settings === undefined ||
        isSettingsView(value.settings)) &&
      (value.error === undefined ||
        typeof value.error === "string") &&
      (value.warning === undefined ||
        typeof value.warning === "string")
    );
  }
  if (value.type === "ling.attachment-saved") {
    return (
      typeof value.requestId === "string" &&
      value.requestId.length > 0 &&
      (value.attachment === undefined ||
        isUserAttachmentView(value.attachment)) &&
      (value.error === undefined ||
        typeof value.error === "string")
    );
  }
  if (value.type === "ling.selection-added") {
    return (
      (value.selection === undefined ||
        isUserSelectionView(value.selection)) &&
      (value.error === undefined ||
        typeof value.error === "string")
    );
  }
  if (value.type === "ling.index-result") {
    return (
      typeof value.ok === "boolean" &&
      (value.stats === undefined || isIndexBuildStats(value.stats)) &&
      (value.error === undefined ||
        typeof value.error === "string")
    );
  }
  if (value.type === "ling.index-status") {
    return isIndexStatus(value.status);
  }
  if (value.type === "ling.mode-changed") {
    return isApprovalMode(value.mode);
  }
  if (value.type === "ling.ready") {
    return true;
  }
  return (
    value.type === "ling.ui-event" &&
    typeof value.sequence === "number" &&
    Number.isInteger(value.sequence) &&
    value.sequence >= 0 &&
    isUiEvent(value.event)
  );
}

export function isUiEvent(value: unknown): value is UiEvent {
  if (!isRecord(value) || typeof value.type !== "string") {
    return false;
  }

  switch (value.type) {
    case "session.snapshot":
      return isSessionSnapshot(value.snapshot);
    case "message.delta":
      return (
        typeof value.messageId === "string" &&
        value.role === "assistant" &&
        typeof value.delta === "string"
      );
    case "thinking.delta":
      return (
        typeof value.messageId === "string" &&
        typeof value.delta === "string"
      );
    case "thinking.completed":
      return typeof value.messageId === "string";
    case "message.completed":
      return typeof value.messageId === "string";
    case "tool.updated":
      return isToolCall(value.tool);
    case "subagent.trace":
      return (
        typeof value.callId === "string" &&
        value.callId.length > 0 &&
        isSubagentTraceEntry(value.entry)
      );
    case "plan.created":
      return (
        typeof value.path === "string" &&
        value.path.length > 0 &&
        typeof value.sessionId === "string" &&
        value.sessionId.length > 0
      );
    case "approval.requested":
      return isApprovalRequest(value.request);
    case "approval.resolved":
      return (
        typeof value.approvalId === "string" &&
        typeof value.approved === "boolean"
      );
    case "question.requested":
      return isQuestionRequest(value.question);
    case "question.resolved":
      return (
        typeof value.callId === "string" &&
        value.callId.length > 0 &&
        (value.answers === undefined ||
          (Array.isArray(value.answers) &&
            value.answers.every(isAskUserAnswer))) &&
        (value.cancelled === undefined ||
          typeof value.cancelled === "boolean")
      );
    case "session.queue":
      return (
        Array.isArray(value.queuedMessages) &&
        value.queuedMessages.every(isQueuedMessage)
      );
    case "run.cancelled":
      return true;
    case "run.failed":
      return typeof value.message === "string";
    default:
      return false;
  }
}

export function isIndexStatus(value: unknown): value is IndexStatus {
  return (
    isRecord(value) &&
    isNonNegativeInteger(value.files) &&
    isNonNegativeInteger(value.totalFiles) &&
    isNonNegativeInteger(value.chunks) &&
    isNonNegativeInteger(value.vectors) &&
    isNonNegativeInteger(value.indexedFiles) &&
    isNonNegativeInteger(value.percent) &&
    value.percent <= 100 &&
    (value.model === null || typeof value.model === "string")
  );
}

function isIndexBuildStats(value: unknown): value is IndexBuildStats {
  if (!isIndexStatus(value)) {
    return false;
  }
  const record = value as unknown as Record<string, unknown>;
  return (
    isNonNegativeInteger(record.embedded) &&
    (record.error === undefined || typeof record.error === "string")
  );
}

function isSettingsView(value: unknown): value is SettingsView {
  return (
    isRecord(value) &&
    typeof value.apiKey === "string" &&
    typeof value.model === "string" &&
    typeof value.baseUrl === "string" &&
    isEffortLevel(value.effort) &&
    isDiffView(value.diffView) &&
    isPositiveInteger(value.maxSteps) &&
    isPositiveInteger(value.maxParallelTools) &&
    isPositiveNumber(value.commandTimeoutSeconds) &&
    isPositiveNumber(value.approvalTimeoutSeconds) &&
    isPositiveInteger(value.contextWindowTokens) &&
    isNonNegativeInteger(value.keepLastUserTurns) &&
    typeof value.embeddingModel === "string" &&
    typeof value.embeddingBaseUrl === "string" &&
    typeof value.embeddingApiKey === "string" &&
    Array.isArray(value.skills) &&
    value.skills.every(isSkillStatus) &&
    Array.isArray(value.memories) &&
    value.memories.every(isMemoryStatus) &&
    Array.isArray(value.memoryDiagnostics) &&
    value.memoryDiagnostics.every(
      (item) => typeof item === "string",
    ) &&
    Array.isArray(value.mcpServers) &&
    value.mcpServers.every(isMcpServerStatus) &&
    Array.isArray(value.mcpIssues) &&
    value.mcpIssues.every(isMcpIssue) &&
    typeof value.mcpConfigPath === "string"
  );
}

function isMcpServerStatus(value: unknown): value is McpServerStatus {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    value.name.length > 0 &&
    (value.kind === "stdio" || value.kind === "http") &&
    typeof value.target === "string" &&
    typeof value.enabled === "boolean" &&
    [
      "connected",
      "configured",
      "not-connected",
      "needs-auth",
      "disabled",
      "error",
    ].includes(String(value.state)) &&
    (value.auth === "none" || value.auth === "oauth") &&
    (value.authorized === undefined ||
      typeof value.authorized === "boolean") &&
    isNonNegativeInteger(value.toolCount) &&
    Array.isArray(value.tools) &&
    value.tools.every((item) => typeof item === "string") &&
    (value.error === undefined || typeof value.error === "string")
  );
}

function isMcpIssue(value: unknown): value is McpIssue {
  return (
    isRecord(value) &&
    (value.name === undefined || typeof value.name === "string") &&
    typeof value.message === "string"
  );
}

function isMemoryStatus(value: unknown): value is MemoryStatus {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    value.name.length > 0 &&
    typeof value.description === "string" &&
    typeof value.path === "string" &&
    value.path.length > 0 &&
    typeof value.relativePath === "string" &&
    (value.scope === "workspace" || value.scope === "global") &&
    typeof value.alwaysApply === "boolean" &&
    Array.isArray(value.globs) &&
    value.globs.every((item) => typeof item === "string") &&
    (value.error === undefined || typeof value.error === "string")
  );
}

function isSkillStatus(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    (value.source === "builtin" ||
      value.source === "global" ||
      value.source === "workspace") &&
    typeof value.path === "string"
  );
}

function isSessionSnapshot(value: unknown): value is SessionSnapshot {
  return (
    isRecord(value) &&
    isRunPhase(value.phase) &&
    Array.isArray(value.entries) &&
    value.entries.every(isTimelineEntry) &&
    (value.runs === undefined ||
      (Array.isArray(value.runs) &&
        value.runs.every(isTimelineRun))) &&
    Array.isArray(value.pendingApprovals) &&
    value.pendingApprovals.every(isApprovalRequest) &&
    (value.pendingQuestions === undefined ||
      (Array.isArray(value.pendingQuestions) &&
        value.pendingQuestions.every(isQuestionRequest))) &&
    (value.queuedMessages === undefined ||
      (Array.isArray(value.queuedMessages) &&
        value.queuedMessages.every(isQueuedMessage))) &&
    (value.approvalMode === undefined ||
      isApprovalMode(value.approvalMode))
  );
}

function isTimelineRun(value: unknown): value is TimelineRunView {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.length > 0 &&
    isTimelineRunStatus(value.status) &&
    (value.startedAt === undefined ||
      (typeof value.startedAt === "number" &&
        Number.isFinite(value.startedAt))) &&
    (value.completedAt === undefined ||
      (typeof value.completedAt === "number" &&
        Number.isFinite(value.completedAt))) &&
    (value.changes === undefined ||
      (Array.isArray(value.changes) &&
        value.changes.every(isWorkspaceChangeSummary))) &&
    (value.changesReverted === undefined ||
      typeof value.changesReverted === "boolean")
  );
}

function isWorkspaceChangeSummary(
  value: unknown,
): value is WorkspaceChangeSummary {
  return (
    isRecord(value) &&
    typeof value.path === "string" &&
    value.path.length > 0 &&
    isNonNegativeInteger(value.added) &&
    isNonNegativeInteger(value.removed)
  );
}

function isTimelineRunStatus(
  value: unknown,
): value is TimelineRunView["status"] {
  return (
    value === "running" ||
    value === "completed" ||
    value === "cancelled" ||
    value === "error"
  );
}

function isQuestionRequest(value: unknown): value is QuestionRequest {
  return (
    isRecord(value) &&
    typeof value.callId === "string" &&
    value.callId.length > 0 &&
    Array.isArray(value.questions) &&
    value.questions.length > 0 &&
    value.questions.every(isAskUserQuestion)
  );
}

function isAskUserQuestion(value: unknown): value is AskUserQuestion {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.length > 0 &&
    typeof value.question === "string" &&
    value.question.length > 0 &&
    (value.header === undefined || typeof value.header === "string") &&
    (value.multiSelect === undefined ||
      typeof value.multiSelect === "boolean") &&
    Array.isArray(value.options) &&
    value.options.every(
      (option) =>
        isRecord(option) &&
        typeof option.label === "string" &&
        option.label.length > 0 &&
        (option.description === undefined ||
          typeof option.description === "string"),
    )
  );
}

function isQueuedMessage(value: unknown): value is QueuedMessage {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.length > 0 &&
    typeof value.text === "string" &&
    typeof value.attachmentCount === "number" &&
    Number.isInteger(value.attachmentCount) &&
    value.attachmentCount >= 0
  );
}

function isAskUserAnswer(value: unknown): value is AskUserAnswer {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id.length > 0 &&
    (value.selected === undefined ||
      (Array.isArray(value.selected) &&
        value.selected.every(
          (selection) => typeof selection === "string",
        ))) &&
    (value.custom === undefined || typeof value.custom === "string")
  );
}

function isTimelineEntry(value: unknown): value is TimelineEntry {
  if (!isRecord(value)) {
    return false;
  }
  if (
    value.runId !== undefined &&
    (typeof value.runId !== "string" || value.runId.length === 0)
  ) {
    return false;
  }

  if (value.kind === "message") {
    return (
      isRecord(value.message) &&
      typeof value.message.id === "string" &&
      (value.message.role === "user" ||
        value.message.role === "assistant") &&
      typeof value.message.content === "string" &&
      (value.message.thinking === undefined ||
        typeof value.message.thinking === "string") &&
      (value.message.thinkingDone === undefined ||
        typeof value.message.thinkingDone === "boolean") &&
      typeof value.message.streaming === "boolean" &&
      (value.message.attachments === undefined ||
        (Array.isArray(value.message.attachments) &&
          value.message.attachments.every(isUserAttachmentView))) &&
      (value.message.selections === undefined ||
        (Array.isArray(value.message.selections) &&
          value.message.selections.every(isUserSelectionSummary)))
    );
  }

  if (value.kind === "tool") {
    return isToolCall(value.tool);
  }

  if (value.kind === "approval") {
    return (
      isRecord(value.approval) &&
      typeof value.approval.id === "string" &&
      typeof value.approval.approvalId === "string" &&
      typeof value.approval.approved === "boolean" &&
      typeof value.approval.target === "string" &&
      isToolAction(value.approval.action)
    );
  }

  if (value.kind === "compaction") {
    return (
      isRecord(value.compaction) &&
      typeof value.compaction.id === "string" &&
      typeof value.compaction.compactionId === "string" &&
      typeof value.compaction.replacedCount === "number"
    );
  }

  return false;
}

function isApprovalRequest(value: unknown): value is ApprovalRequest {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.tool === "string" &&
    isToolAction(value.action) &&
    typeof value.target === "string" &&
    typeof value.reason === "string" &&
    (value.arguments === undefined || isRecord(value.arguments)) &&
    typeof value.effectDigest === "string" &&
    isStringArray(value.details)
  );
}

function isToolCall(value: unknown): value is ToolCall {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    (value.name === undefined ||
      (typeof value.name === "string" && value.name.length > 0)) &&
    isToolAction(value.action) &&
    typeof value.target === "string" &&
    isToolStatus(value.status) &&
    isStringArray(value.details) &&
    (value.detail === undefined || isToolDetail(value.detail))
  );
}

function isToolDetail(value: unknown): value is ToolDetail {
  if (!isRecord(value)) {
    return false;
  }
  if (value.kind === "diff") {
    return (
      typeof value.path === "string" &&
      Array.isArray(value.rows) &&
      value.rows.every((row) => isToolDiffRow(row)) &&
      typeof value.added === "number" &&
      typeof value.removed === "number" &&
      (value.truncated === undefined ||
        typeof value.truncated === "boolean")
    );
  }
  if (value.kind === "io") {
    return (
      (value.input === undefined || typeof value.input === "string") &&
      (value.output === undefined || typeof value.output === "string") &&
      (value.error === undefined || typeof value.error === "string") &&
      (value.exitCode === undefined ||
        typeof value.exitCode === "number")
    );
  }
  if (value.kind === "text") {
    return typeof value.text === "string";
  }
  if (value.kind === "plan") {
    return (
      typeof value.path === "string" &&
      typeof value.text === "string"
    );
  }
  if (value.kind === "todo") {
    return (
      Array.isArray(value.todos) &&
      value.todos.every(isToolTodoItem)
    );
  }
  if (value.kind === "subagent") {
    return (
      typeof value.description === "string" &&
      typeof value.subagentType === "string" &&
      typeof value.mode === "string" &&
      (value.summary === undefined || typeof value.summary === "string") &&
      (value.trace === undefined ||
        (Array.isArray(value.trace) &&
          value.trace.every(isSubagentTraceEntry))) &&
      (value.error === undefined || typeof value.error === "string")
    );
  }
  if (value.kind === "memory") {
    return (
      (value.operation === "list" ||
        value.operation === "write" ||
        value.operation === "delete") &&
      typeof value.ok === "boolean" &&
      (value.scope === undefined ||
        value.scope === "workspace" ||
        value.scope === "global") &&
      (value.name === undefined || typeof value.name === "string") &&
      (value.path === undefined || typeof value.path === "string") &&
      (value.memories === undefined ||
        (Array.isArray(value.memories) &&
          value.memories.every(isMemoryDetailItem))) &&
      (value.diagnostics === undefined ||
        (Array.isArray(value.diagnostics) &&
          value.diagnostics.every(
            (item) => typeof item === "string",
          ))) &&
      (value.error === undefined || typeof value.error === "string")
    );
  }
  if (value.kind === "skill") {
    return (
      typeof value.name === "string" &&
      typeof value.description === "string" &&
      typeof value.body === "string" &&
      typeof value.skillPath === "string" &&
      (value.scriptsPath === undefined ||
        typeof value.scriptsPath === "string") &&
      (value.truncated === undefined ||
        typeof value.truncated === "boolean") &&
      typeof value.ok === "boolean" &&
      (value.error === undefined || typeof value.error === "string")
    );
  }
  return false;
}

function isMemoryDetailItem(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.description === "string" &&
    (value.scope === "workspace" || value.scope === "global") &&
    typeof value.alwaysApply === "boolean" &&
    Array.isArray(value.globs) &&
    value.globs.every((item) => typeof item === "string") &&
    typeof value.path === "string"
  );
}

function isSubagentTraceEntry(
  value: unknown,
): value is SubagentTraceEntry {
  if (!isRecord(value)) {
    return false;
  }
  if (value.kind === "assistant") {
    return (
      typeof value.id === "string" &&
      typeof value.text === "string" &&
      (value.thinking === undefined ||
        typeof value.thinking === "string")
    );
  }
  if (value.kind === "tool_call") {
    return (
      typeof value.id === "string" &&
      typeof value.name === "string" &&
      isRecord(value.arguments)
    );
  }
  if (value.kind === "tool_result") {
    return (
      typeof value.id === "string" &&
      typeof value.name === "string" &&
      (value.output === undefined || typeof value.output === "string") &&
      typeof value.isError === "boolean"
    );
  }
  if (value.kind === "error") {
    return typeof value.message === "string";
  }
  return false;
}

function isToolTodoItem(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.content === "string" &&
    (value.status === "pending" ||
      value.status === "in_progress" ||
      value.status === "completed" ||
      value.status === "cancelled")
  );
}

function isToolDiffRow(value: unknown): value is ToolDiffRow {
  return (
    isRecord(value) &&
    (value.type === "context" ||
      value.type === "add" ||
      value.type === "remove") &&
    (value.oldLine === undefined || typeof value.oldLine === "number") &&
    (value.newLine === undefined || typeof value.newLine === "number") &&
    typeof value.text === "string"
  );
}

function isApprovalMode(value: unknown): value is ApprovalMode {
  return (
    value === "manual" ||
    value === "accept-write" ||
    value === "auto" ||
    value === "plan"
  );
}

function isEffortLevel(value: unknown): value is EffortLevel {
  return (
    value === "low" ||
    value === "medium" ||
    value === "high" ||
    value === "xhigh" ||
    value === "max"
  );
}

function isDiffView(value: unknown): value is DiffView {
  return value === "inline" || value === "sideBySide";
}

function isPositiveInteger(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 1
  );
}

function isNonNegativeInteger(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0
  );
}

function isPositiveNumber(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value > 0
  );
}

function isRunPhase(value: unknown): value is RunPhase {
  return (
    value === "idle" ||
    value === "running" ||
    value === "waiting_approval" ||
    value === "cancelled" ||
    value === "failed"
  );
}

function isToolAction(value: unknown): value is ToolAction {
  return (
    value === "Read" ||
    value === "Edit" ||
    value === "Run" ||
    value === "Skill" ||
    value === "Plan" ||
    value === "Subagent" ||
    value === "Browser" ||
    value === "Web" ||
    value === "Memory" ||
    value === "MCP"
  );
}

function isToolStatus(value: unknown): value is ToolStatus {
  return (
    value === "Running" ||
    value === "Waiting" ||
    value === "Completed" ||
    value === "Failed" ||
    value === "Cancelled"
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isUserAttachmentRef(
  value: unknown,
): value is UserAttachmentRef {
  if (!isRecord(value)) {
    return false;
  }
  if (
    typeof value.relativePath !== "string" ||
    value.relativePath.length === 0 ||
    typeof value.name !== "string" ||
    value.name.length === 0 ||
    typeof value.mediaType !== "string" ||
    value.mediaType.length === 0
  ) {
    return false;
  }
  const kind = value.kind ?? "image";
  if (kind !== "image" && kind !== "file") {
    return false;
  }
  return kind !== "image" || value.mediaType.startsWith("image/");
}

function isUserAttachmentView(
  value: unknown,
): value is UserAttachmentView {
  if (!isRecord(value) || !isUserAttachmentRef(value)) {
    return false;
  }
  return value.uri === undefined || typeof value.uri === "string";
}

const SELECTION_KINDS: readonly UserSelectionKind[] = [
  "editor",
  "terminal",
  "chat",
];

function isUserSelectionKind(value: unknown): value is UserSelectionKind {
  return SELECTION_KINDS.includes(value as UserSelectionKind);
}

function isUserSelectionRef(
  value: unknown,
): value is UserSelectionRef {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isUserSelectionKind(value.kind) &&
    typeof value.label === "string" &&
    value.label.length > 0 &&
    typeof value.text === "string" &&
    value.text.length > 0 &&
    typeof value.lineCount === "number" &&
    Number.isInteger(value.lineCount) &&
    value.lineCount >= 1 &&
    (value.path === undefined || typeof value.path === "string") &&
    (value.startLine === undefined ||
      (typeof value.startLine === "number" &&
        Number.isInteger(value.startLine) &&
        value.startLine >= 1)) &&
    (value.endLine === undefined ||
      (typeof value.endLine === "number" &&
        Number.isInteger(value.endLine) &&
        value.endLine >= 1))
  );
}

function isUserSelectionView(
  value: unknown,
): value is UserSelectionView {
  if (!isRecord(value) || !isUserSelectionRef(value)) {
    return false;
  }
  return typeof value.id === "string" && value.id.length > 0;
}

function isUserSelectionSummary(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isUserSelectionKind(value.kind) &&
    typeof value.label === "string" &&
    value.label.length > 0 &&
    typeof value.lineCount === "number" &&
    Number.isInteger(value.lineCount) &&
    value.lineCount >= 1 &&
    (value.path === undefined || typeof value.path === "string") &&
    (value.startLine === undefined ||
      (typeof value.startLine === "number" &&
        Number.isInteger(value.startLine) &&
        value.startLine >= 1)) &&
    (value.endLine === undefined ||
      (typeof value.endLine === "number" &&
        Number.isInteger(value.endLine) &&
        value.endLine >= 1))
  );
}

function isHistorySession(value: unknown): value is HistorySession {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.workspaceRoot === "string" &&
    typeof value.updatedAt === "number" &&
    typeof value.status === "string" &&
    (value.snippet === undefined ||
      typeof value.snippet === "string")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

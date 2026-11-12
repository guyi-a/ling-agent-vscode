export type ApprovalMode = "manual" | "accept-write" | "auto" | "plan";

export type ToolAction =
  | "Read"
  | "Edit"
  | "Run"
  | "Skill"
  | "Plan"
  | "Subagent";

export type ToolStatus =
  | "Running"
  | "Waiting"
  | "Completed"
  | "Failed"
  | "Cancelled";

export interface ToolDiffRow {
  type: "context" | "add" | "remove";
  oldLine?: number;
  newLine?: number;
  text: string;
}

export type SubagentTraceEntry =
  | {
      kind: "assistant";
      id: string;
      text: string;
      thinking?: string;
    }
  | {
      kind: "tool_call";
      id: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | {
      kind: "tool_result";
      id: string;
      name: string;
      output?: string;
      isError: boolean;
    }
  | {
      kind: "error";
      message: string;
    };

export type ToolDetail =
  | {
      kind: "diff";
      path: string;
      rows: ToolDiffRow[];
      added: number;
      removed: number;
      truncated?: boolean;
    }
  | {
      kind: "io";
      input?: string;
      output?: string;
      error?: string;
      exitCode?: number;
    }
  | {
      kind: "text";
      text: string;
    }
  | {
      kind: "plan";
      path: string;
      text: string;
    }
  | {
      kind: "todo";
      todos: ToolTodoItem[];
    }
  | {
      kind: "subagent";
      description: string;
      subagentType: string;
      mode: string;
      summary?: string;
      trace?: SubagentTraceEntry[];
      error?: string;
    };

export interface ToolTodoItem {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed" | "cancelled";
}

export interface ToolCall {
  id: string;
  action: ToolAction;
  target: string;
  status: ToolStatus;
  details: string[];
  detail?: ToolDetail;
}

export interface ApprovalRequest {
  id: string;
  tool: string;
  action: ToolAction;
  target: string;
  reason: string;
  arguments?: Record<string, unknown>;
  effectDigest: string;
  details: string[];
}

export interface ApprovalResult {
  id: string;
  approvalId: string;
  approved: boolean;
  target: string;
  action: ToolAction;
}

export interface QuestionOption {
  label: string;
  description?: string;
}

export interface AskUserQuestion {
  id: string;
  question: string;
  header?: string;
  options: QuestionOption[];
  multiSelect?: boolean;
}

export interface QuestionRequest {
  callId: string;
  questions: AskUserQuestion[];
}

export interface AskUserAnswer {
  id: string;
  selected?: string[];
  custom?: string;
}

export interface QueuedMessage {
  id: string;
  text: string;
  attachmentCount: number;
}

export type UserSelectionKind = "editor" | "terminal" | "chat";

export interface UserSelectionRef {
  kind: UserSelectionKind;
  label: string;
  text: string;
  lineCount: number;
  path?: string;
  startLine?: number;
  endLine?: number;
}

export interface UserSelectionView extends UserSelectionRef {
  id: string;
}

export interface UserSelectionSummary {
  kind: UserSelectionKind;
  label: string;
  lineCount: number;
  path?: string;
  startLine?: number;
  endLine?: number;
}

export interface CompactionItem {
  id: string;
  compactionId: string;
  replacedCount: number;
}

export type UserAttachmentKind = "image" | "file";

export interface UserAttachmentRef {
  relativePath: string;
  name: string;
  mediaType: string;
  kind?: UserAttachmentKind;
}

export interface UserAttachmentView extends UserAttachmentRef {
  uri?: string;
}

export interface TimelineMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  thinkingDone?: boolean;
  thinkingStartedAt?: number;
  thinkingDurationMs?: number;
  streaming: boolean;
  attachments?: UserAttachmentView[];
  selections?: UserSelectionSummary[];
}

export type TimelineEntry =
  | {
      kind: "message";
      message: TimelineMessage;
    }
  | {
      kind: "tool";
      tool: ToolCall;
    }
  | {
      kind: "approval";
      approval: ApprovalResult;
    }
  | {
      kind: "compaction";
      compaction: CompactionItem;
    };

export type RunPhase =
  | "idle"
  | "running"
  | "waiting_approval"
  | "cancelled"
  | "failed";

export interface SessionSnapshot {
  phase: RunPhase;
  entries: TimelineEntry[];
  pendingApprovals: ApprovalRequest[];
  pendingQuestions?: QuestionRequest[];
  queuedMessages?: QueuedMessage[];
  error?: string | null;
  sessionId?: string;
  readOnly?: boolean;
  approvalMode?: ApprovalMode;
}

export interface HistorySession {
  id: string;
  title: string;
  workspaceRoot: string;
  updatedAt: number;
  status: string;
  snippet?: string;
}

export type EffortLevel =
  | "low"
  | "medium"
  | "high"
  | "xhigh"
  | "max";

export type DiffView = "inline" | "sideBySide";

export interface SkillStatus {
  name: string;
  source: "builtin" | "workspace";
  path: string;
  overridesBuiltin?: boolean;
}

export interface IndexStatus {
  files: number;
  totalFiles: number;
  chunks: number;
  vectors: number;
  indexedFiles: number;
  percent: number;
  model: string | null;
}

export interface IndexBuildStats extends IndexStatus {
  embedded: number;
  error?: string;
}

export interface SettingsView {
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
  skills: SkillStatus[];
}

export interface TimelineState extends SessionSnapshot {
  error: string | null;
  subagentTraces: Record<string, SubagentTraceEntry[]>;
}

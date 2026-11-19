export type JsonObject = Record<string, unknown>;

export interface RunInfo {
  id: string;
  status: string;
  startedAt?: number;
  completedAt?: number;
}

export interface TimelineEntry {
  kind: string;
  runId?: string;
  message?: {
    id?: string;
    role?: string;
    content?: unknown;
    streaming?: boolean;
    thinking?: string;
    thinkingDone?: boolean;
    attachments?: Array<{
      name?: string;
      relativePath?: string;
      kind?: string;
    }>;
  };
  tool?: {
    id?: string;
    name?: string;
    action?: string;
    target?: string;
    status?: string;
    details?: string[];
    detail?: JsonObject;
  };
  approval?: {
    id?: string;
    approvalId?: string;
    approved?: boolean;
    target?: string;
    action?: string;
  };
  [key: string]: unknown;
}

export interface ApprovalAllowAlways {
  kind: "mode" | "command";
  mode?: string;
  label?: string;
}

export interface ApprovalRequest {
  id: string;
  tool: string;
  action?: string;
  target?: string;
  reason?: string;
  arguments?: JsonObject;
  effectDigest: string;
  details?: string[];
  allowAlways?: ApprovalAllowAlways;
  [key: string]: unknown;
}

export interface QuestionOption {
  label: string;
  description?: string;
}

export interface QuestionItem {
  id: string;
  question: string;
  header?: string;
  options?: QuestionOption[];
  multiSelect?: boolean;
}

export interface QuestionRequest {
  callId: string;
  questions: QuestionItem[];
}

export interface QueuedMessage {
  id: string;
  text: string;
  attachmentCount?: number;
}

export interface BackgroundTask {
  id: string;
  description?: string;
  status?: string;
  kind?: string;
  summary?: string | null;
  error?: string | null;
  exitCode?: number | null;
  outputPath?: string | null;
  createdAt?: number;
  startedAt?: number | null;
  completedAt?: number | null;
  [key: string]: unknown;
}

export interface HistorySession {
  id: string;
  title: string;
  workspaceRoot: string;
  updatedAt: number;
  status?: string;
  snippet?: string;
}

export interface SessionSnapshot {
  phase: string;
  entries: TimelineEntry[];
  pendingApprovals: ApprovalRequest[];
  pendingQuestions: QuestionRequest[];
  queuedMessages: QueuedMessage[];
  backgroundTasks: BackgroundTask[];
  computerSession: JsonObject | null;
  error: string | null;
  sessionId?: string | null;
  runs?: RunInfo[];
  readOnly?: boolean;
  approvalMode?: string;
}

export interface UiEvent {
  type: string;
  snapshot?: SessionSnapshot;
  [key: string]: unknown;
}

export interface InitializeResult {
  protocolVersion: number;
  serverInfo: { name: string; version: string };
  capabilities: Record<string, boolean>;
}

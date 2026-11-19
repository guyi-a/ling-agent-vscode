import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { Box, Static, Text, useApp, useInput } from "ink";

import type { SidecarClient } from "../rpc.js";
import type {
  ApprovalRequest,
  BackgroundTask,
  HistorySession,
  JsonObject,
  QuestionItem,
  QuestionRequest,
  QueuedMessage,
  SessionSnapshot,
  TimelineEntry,
  UiEvent,
} from "../types.js";
import { ApprovalDetail, ToolDetail } from "./detail.js";
import { Markdown } from "./markdown.js";

const HELP = [
  "Commands:",
  "  /help                         show this help",
  "  /new                          start a new session",
  "  /history [query]              list or search sessions",
  "  /tasks                        list background tasks",
  "  /stop-task <id>               stop a background task",
  "  /details [n]                  show a tool's diff/io (1 = most recent)",
  "  /last                         show the last tool's details",
  "  /attach <path>                attach a file or image to the next message",
  "  /detach                       clear pending attachments",
  "  /mcp                          show MCP servers and tools",
  "  /index                        build or refresh the code index",
  "  /index-status                 show code index status",
  "  /changes [runId]              show the latest run's file changes",
  "  /mode [manual|accept-write|auto|plan]",
  "  /effort [low|medium|high|xhigh|max]",
  "  /status                       refresh the snapshot",
  "  /quit                         exit",
  "",
  "Keys: Enter send · Shift+Tab mode · Ctrl+Alt+T thinking · Esc interrupt/close · Ctrl+C exit · Ctrl+D exit",
  "",
  "While a run is active, a new message is queued below the conversation.",
  "Esc interrupts the run and promotes the top (newest) queued message.",
].join("\n");

interface CommandSpec {
  name: string;
  description: string;
}

const COMMANDS: CommandSpec[] = [
  { name: "help", description: "show commands and keys" },
  { name: "new", description: "start a new session" },
  { name: "history", description: "list or search sessions" },
  { name: "tasks", description: "list background tasks" },
  { name: "stop-task", description: "stop a background task" },
  { name: "details", description: "show a tool's full diff/io" },
  { name: "last", description: "show the last tool's details" },
  { name: "attach", description: "attach a file or image" },
  { name: "detach", description: "clear pending attachments" },
  { name: "mcp", description: "show MCP servers and tools" },
  { name: "index", description: "build or refresh the code index" },
  { name: "index-status", description: "show code index status" },
  { name: "changes", description: "show the latest run's file changes" },
  { name: "mode", description: "set approval mode" },
  { name: "effort", description: "set reasoning effort" },
  { name: "status", description: "refresh the snapshot" },
  { name: "quit", description: "exit" },
];

const MAX_PENDING_ATTACHMENTS = 4;
const MAX_IMAGE_ATTACHMENT_BYTES = 4 * 1024 * 1024;
const MAX_FILE_ATTACHMENT_BYTES = 25 * 1024 * 1024;

interface PendingAttachment {
  relativePath: string;
  name: string;
  mediaType: string;
  kind: "image" | "file";
}

const ATTACHMENT_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".bmp": "image/bmp",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".json": "application/json",
  ".csv": "text/csv",
  ".log": "text/plain",
  ".py": "text/x-python",
  ".ts": "text/typescript",
  ".tsx": "text/typescript",
  ".js": "text/javascript",
  ".jsx": "text/javascript",
  ".html": "text/html",
  ".css": "text/css",
  ".xml": "application/xml",
  ".yaml": "application/yaml",
  ".yml": "application/yaml",
  ".toml": "application/toml",
  ".sh": "text/x-shellscript",
  ".ps1": "text/x-powershell",
  ".doc": "application/msword",
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xls": "application/vnd.ms-excel",
  ".xlsx":
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".ppt": "application/vnd.ms-powerpoint",
  ".pptx":
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".zip": "application/zip",
};

const IMAGE_ATTACHMENT_EXTENSIONS: Record<string, string> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/bmp": ".bmp",
};

function attachmentMime(fileName: string): string {
  return (
    ATTACHMENT_MIME[path.extname(fileName).toLowerCase()] ??
    "application/octet-stream"
  );
}

function attachmentStem(fileName: string): string {
  const base = path.basename(fileName).replace(/\.[^.]*$/, "");
  const sanitized = base
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return sanitized || "image";
}

async function prepareAttachment(
  filePath: string,
  workspace: string,
): Promise<PendingAttachment> {
  const raw = filePath.trim().replace(/^["']|["']$/g, "");
  if (!raw) {
    throw new Error("path is empty");
  }
  const absolute = path.isAbsolute(raw)
    ? raw
    : path.resolve(workspace, raw);
  const info = await stat(absolute);
  if (!info.isFile()) {
    throw new Error("path is not a file");
  }
  const name = path.basename(absolute);
  const mediaType = attachmentMime(name);
  if (mediaType.startsWith("image/")) {
    const extension = IMAGE_ATTACHMENT_EXTENSIONS[mediaType];
    if (!extension) {
      throw new Error(`unsupported image type: ${mediaType}`);
    }
    if (info.size > MAX_IMAGE_ATTACHMENT_BYTES) {
      throw new Error("image exceeds 4 MB");
    }
    const bytes = await readFile(absolute);
    const directory = path.join(workspace, ".agents", "attachments");
    await mkdir(directory, { recursive: true });
    const fileName = `${attachmentStem(name)}-${randomUUID().slice(0, 8)}${extension}`;
    await writeFile(path.join(directory, fileName), bytes);
    return {
      relativePath: `.agents/attachments/${fileName}`,
      name,
      mediaType,
      kind: "image",
    };
  }
  if (info.size > MAX_FILE_ATTACHMENT_BYTES) {
    throw new Error("file exceeds 25 MB");
  }
  return {
    relativePath: absolute,
    name,
    mediaType,
    kind: "file",
  };
}

const MODES = ["manual", "accept-write", "auto", "plan"] as const;

const MODE_COLORS: Record<string, string> = {
  manual: "blue",
  "accept-write": "yellow",
  auto: "green",
  plan: "magenta",
};

const TOOL_COLORS: Record<string, string> = {
  read: "blue",
  edit: "yellow",
  write: "yellow",
  run: "cyan",
  skill: "magenta",
  plan: "cyan",
  todo: "cyan",
  subagent: "magenta",
  browser: "blue",
  web: "blue",
  memory: "blue",
  mcp: "green",
  computer: "red",
};

function toolColor(action: string | undefined): string {
  return TOOL_COLORS[String(action ?? "").toLowerCase()] ?? "cyan";
}

function toolStatusColor(status: string): string {
  if (status === "Completed") {
    return "green";
  }
  if (status === "Failed" || status === "Cancelled") {
    return "red";
  }
  return "yellow";
}

const SHIFT_TAB = String.fromCharCode(27) + "[Z";
const HIDE_CURSOR = String.fromCharCode(27) + "[?25l";
const SHOW_CURSOR = String.fromCharCode(27) + "[?25h";

interface AppProps {
  client: SidecarClient;
  workspace: string;
  model: string;
  initialMode: string;
  initialEffort: string;
  initialSnapshot: SessionSnapshot;
}

interface QuestionDraft {
  callId: string;
  index: number;
  selected: Record<string, string[]>;
  customs: Record<string, string>;
  customMode: boolean;
  customInput: string;
}

interface Overlay {
  title: string;
  lines: string[];
  tool?: TimelineEntry;
  sessions?: HistorySession[];
  selected?: number;
}

function messageText(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") {
          return block;
        }
        if (block && typeof block === "object" && "text" in block) {
          return String((block as { text?: unknown }).text ?? "");
        }
        return "";
      })
      .join("");
  }
  return "";
}

function truncate(value: string, limit = 120): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function clip(value: string, limit: number): string {
  return value.length <= limit
    ? value
    : `${value.slice(0, Math.max(0, limit - 1))}…`;
}

function shortId(value: string, length = 18): string {
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

function toolTitle(entry: TimelineEntry): string {
  const tool = entry.tool ?? {};
  return `Tool ${tool.action ?? "Tool"} ${tool.target ?? ""}`.trim();
}

function indexStatusLines(status: Record<string, unknown>): string[] {
  const files = Number(status.files ?? 0);
  const totalFiles = Number(status.totalFiles ?? 0);
  const chunks = Number(status.chunks ?? 0);
  const vectors = Number(status.vectors ?? 0);
  const indexedFiles = Number(status.indexedFiles ?? 0);
  const percent = Number(status.percent ?? 0);
  const model = status.model ? String(status.model) : "BM25 only";
  const lines = [
    `files ${files}/${totalFiles} · ${percent}%`,
    `chunks ${chunks} · vectors ${vectors} · indexed ${indexedFiles}`,
    `model ${model}`,
  ];
  if (typeof status.embedded === "number") {
    lines.push(`embedded ${status.embedded}`);
  }
  if (status.error) {
    lines.push(`error: ${String(status.error)}`);
  }
  return lines;
}

function formatTime(value: number | undefined): string {
  if (!value) {
    return "";
  }
  return new Date(value).toISOString().replace("T", " ").slice(0, 19);
}

function entryKey(entry: TimelineEntry, index: number): string {
  return [
    entry.kind,
    entry.message?.id,
    entry.tool?.id,
    entry.approval?.id,
    index,
  ]
    .filter(Boolean)
    .join(":");
}

function isFinalEntry(entry: TimelineEntry): boolean {
  if (entry.kind === "message") {
    if (entry.message?.role === "user") {
      return true;
    }
    if (entry.message?.role === "assistant") {
      const message = entry.message;
      const hasToolCall =
        Array.isArray(message.content) &&
        message.content.some((block) => {
          if (!block || typeof block !== "object") {
            return false;
          }
          const type = String(
            (block as { type?: unknown }).type ?? "",
          );
          return type === "tool_call" || type === "tool_use";
        });
      return (
        message.streaming !== true &&
        (Boolean(messageText(message.content)) ||
          Boolean(message.thinking?.trim()) ||
          hasToolCall)
      );
    }
    return false;
  }
  if (entry.kind === "tool") {
    return ["Completed", "Failed", "Cancelled"].includes(
      String(entry.tool?.status ?? ""),
    );
  }
  if (entry.kind === "approval" || entry.kind === "compaction") {
    return true;
  }
  return false;
}

function heldAssistantStaticKey(entries: TimelineEntry[]): string | null {
  const last = entries[entries.length - 1];
  if (
    !last ||
    last.kind !== "message" ||
    last.message?.role !== "assistant" ||
    !isFinalEntry(last)
  ) {
    return null;
  }
  return staticEntryKey(last);
}

function staticEntryKey(entry: TimelineEntry): string {
  if (entry.message?.id) {
    return `message:${entry.message.id}`;
  }
  if (entry.tool?.id) {
    return `tool:${entry.tool.id}`;
  }
  if (entry.approval?.id) {
    return `approval:${entry.approval.id}`;
  }
  if (entry.kind === "compaction") {
    const compaction = entry.compaction as
      | Record<string, unknown>
      | undefined;
    return `compaction:${String(
      compaction?.id ?? entry.runId ?? JSON.stringify(entry).slice(0, 80),
    )}`;
  }
  return `entry:${JSON.stringify(entry).slice(0, 120)}`;
}

function taskLines(tasks: BackgroundTask[]): string[] {
  return tasks.map(
    (task) =>
      `${shortId(task.id)}  ${task.status ?? "?"}  ${
        task.description ?? ""
      }${
        task.exitCode !== null && task.exitCode !== undefined
          ? `  exit=${task.exitCode}`
          : ""
      }${task.error ? `  ${truncate(task.error, 80)}` : ""}`,
  );
}

function currentQuestion(
  request: QuestionRequest,
  draft: QuestionDraft,
): QuestionItem | undefined {
  return request.questions[draft.index];
}

function EntryView({
  entry,
  deltas,
  thinking,
  phase,
  width = 80,
  nested = false,
  turnHeader = false,
  showThinking = false,
  turnThinking,
}: {
  entry: TimelineEntry;
  deltas: Map<string, string>;
  thinking: Map<string, string>;
  phase: string;
  width?: number;
  nested?: boolean;
  turnHeader?: boolean;
  showThinking?: boolean;
  turnThinking?: string;
}) {
  if (entry.kind === "message") {
    const message = entry.message ?? {};
    const content = messageText(message.content);
    if (message.role === "user") {
      const attached = Array.isArray(message.attachments)
        ? message.attachments
            .map((attachment) => attachment.name)
            .filter((name): name is string => Boolean(name))
        : [];
      return (
        <Box marginTop={1}>
          <Text>
            <Text color="green" bold>
              User:
            </Text>
            {` ${content || attached.join(", ") || "(attachment)"}`}
            {attached.length > 0 && content ? (
              <Text color="cyan">{`  [${attached.join(", ")}]`}</Text>
            ) : null}
          </Text>
        </Box>
      );
    }
    if (message.role === "assistant") {
      const id = message.id ?? "";
      const streamed = content || deltas.get(id) || "";
      const storedThinking = (
        message as { thinking?: unknown }
      ).thinking;
      const thinkingText =
        turnThinking ||
        thinking.get(id) ||
        (typeof storedThinking === "string" ? storedThinking : "");
      const thinkingExpanded = showThinking && Boolean(thinkingText);
      const thinkingBlock = thinkingExpanded ? (
        <Box flexDirection="column">
          <Text dimColor>✻ Thinking</Text>
          <Text dimColor italic>
            {thinkingText}
          </Text>
        </Box>
      ) : null;
      const turnLabel = (
        <Text color="cyan" bold>
          Ling:
        </Text>
      );
      if (!streamed) {
        if (thinkingExpanded) {
          return (
            <Box marginTop={1} flexDirection="column">
              {turnHeader || !nested ? turnLabel : null}
              <Box paddingLeft={2}>{thinkingBlock}</Box>
            </Box>
          );
        }
        if (
          (thinkingText || thinking.has(id)) &&
          phase === "running"
        ) {
          return (
            <Box marginTop={1} flexDirection="column">
              {turnHeader || !nested ? turnLabel : null}
              <Text dimColor>{"  "}Thinking…</Text>
            </Box>
          );
        }
        return null;
      }
      if (nested) {
        return (
          <Box marginTop={1} flexDirection="column">
            {turnHeader ? turnLabel : null}
            <Box paddingLeft={2} flexDirection="column">
              {thinkingBlock}
              <Markdown
                text={streamed}
                width={Math.max(20, width - 2)}
              />
            </Box>
          </Box>
        );
      }
      if (thinkingExpanded) {
        return (
          <Box marginTop={1} flexDirection="column">
            {turnLabel}
            <Box paddingLeft={2} flexDirection="column">
              {thinkingBlock}
              <Markdown
                text={streamed}
                width={Math.max(20, width - 2)}
              />
            </Box>
          </Box>
        );
      }
      return (
        <Box marginTop={1} flexDirection="row">
          {turnLabel}
          <Box
            flexDirection="column"
            flexGrow={1}
            flexShrink={1}
            paddingLeft={1}
          >
            <Markdown text={streamed} width={width} />
          </Box>
        </Box>
      );
    }
    return null;
  }

  if (entry.kind === "tool") {
    const tool = entry.tool ?? {};
    const status = tool.status ?? "Running";
    const failed = status === "Failed" || status === "Cancelled";
    const detail = tool.detail ?? {};
    let suffix = "";
    if (detail.kind === "diff") {
      suffix = ` +${String(detail.added ?? 0)} −${String(
        detail.removed ?? 0,
      )}`;
    }
    const errorSource = failed
      ? String(
          detail.output ??
            (Array.isArray(tool.details)
              ? tool.details[tool.details.length - 1]
              : "") ??
            "",
        )
      : "";
    const errorLine = errorSource.replace(/^(?:Error:\s*)+/i, "");
    return (
      <Box marginTop={1} flexDirection="column">
        {turnHeader ? (
          <Text color="cyan" bold>
            Ling:
          </Text>
        ) : null}
        <Text>
          {"  "}
          <Text bold color={toolColor(tool.action)}>
            {tool.action ?? "Tool"}
          </Text>
          <Text bold>{` ${tool.target ?? ""}`}</Text>{" "}
          <Text color={toolStatusColor(status)}>{status}</Text>
          <Text dimColor>{suffix}</Text>
        </Text>
        {errorLine ? (
          <Text color="red" dimColor>
            {"  "}
            {truncate(errorLine, 160)}
          </Text>
        ) : null}
      </Box>
    );
  }

  if (entry.kind === "approval") {
    const approval = entry.approval ?? {};
    return (
      <Box flexDirection="column">
        {turnHeader ? (
          <Text color="cyan" bold>
            Ling:
          </Text>
        ) : null}
        <Text color={approval.approved ? "green" : "red"} dimColor>
          {"  "}
          {approval.approved ? "✓" : "✕"} {approval.action ?? "Approval"}{" "}
          {approval.target ?? ""}
        </Text>
      </Box>
    );
  }

  if (entry.kind === "compaction") {
    return (
      <Box flexDirection="column">
        {turnHeader ? (
          <Text color="cyan" bold>
            Ling:
          </Text>
        ) : null}
        <Text dimColor>
          {"  "}… context compacted
        </Text>
      </Box>
    );
  }

  return null;
}

function ApprovalCard({
  approval,
  feedbackMode,
  feedback,
}: {
  approval: ApprovalRequest;
  feedbackMode: boolean;
  feedback: string;
}) {
  return (
    <Box
      marginTop={1}
      borderStyle="round"
      borderColor="yellow"
      flexDirection="column"
      paddingX={1}
    >
      <Text bold color="yellow">
        Approval required · {approval.tool}
      </Text>
      <Text>
        Target: <Text bold>{approval.target ?? "(unknown)"}</Text>
      </Text>
      {approval.reason ? (
        <Text dimColor>{truncate(approval.reason, 160)}</Text>
      ) : null}
      <ApprovalDetail approval={approval} />
      {feedbackMode ? (
        <Text>
          Feedback:{" "}
          <Text color="red">{feedback || "(type and press Enter)"}</Text>
        </Text>
      ) : (
        <Text>
          <Text color="green">[1]</Text> Allow once{" "}
          {approval.allowAlways ? (
            <>
              <Text color="green">[2]</Text>{" "}
              {approval.allowAlways.label ?? "Allow always"}{" "}
            </>
          ) : null}
          <Text color="red">[3]</Text> Deny <Text color="red">[4]</Text>{" "}
          Deny with feedback
        </Text>
      )}
    </Box>
  );
}

function QuestionCard({
  request,
  draft,
  busy,
}: {
  request: QuestionRequest;
  draft: QuestionDraft;
  busy: boolean;
}) {
  const question = currentQuestion(request, draft);
  if (!question) {
    return null;
  }
  const options = question.options ?? [];
  const selected = new Set(draft.selected[question.id] ?? []);
  const custom = draft.customs[question.id];
  return (
    <Box
      marginTop={1}
      borderStyle="round"
      borderColor="magenta"
      flexDirection="column"
      paddingX={1}
    >
      <Text bold color="magenta">
        {question.header ?? "Question"}{" "}
        <Text dimColor>
          ({draft.index + 1}/{request.questions.length})
        </Text>
      </Text>
      <Text>{question.question}</Text>
      {options.length > 0 ? (
        options.map((option, index) => {
          const checked = selected.has(option.label);
          const marker = question.multiSelect
            ? checked
              ? "[x]"
              : "[ ]"
            : checked
              ? "(•)"
              : "( )";
          return (
            <Text key={`${option.label}:${index}`}>
              <Text color="cyan">[{index + 1}]</Text> {marker}{" "}
              {option.label}
              {option.description ? (
                <Text dimColor> — {option.description}</Text>
              ) : null}
            </Text>
          );
        })
      ) : null}
      {custom ? <Text dimColor>Other: {custom}</Text> : null}
      {draft.customMode ? (
        <Text>
          Custom:{" "}
          <Text color="cyan">
            {draft.customInput || "(type and press Enter)"}
          </Text>
        </Text>
      ) : options.length === 0 ? (
        <Text>
          Answer:{" "}
          <Text color="cyan">
            {draft.customInput || "(type and press Enter)"}
          </Text>
        </Text>
      ) : null}
      <Text dimColor>
        {question.multiSelect
          ? "1-9 toggle · Enter next · c custom · Esc cancel"
          : options.length > 0
            ? "1-9 select · c custom · Esc cancel"
            : "Type answer · Enter next · Esc cancel"}
      </Text>
      {busy ? <Text dimColor>Submitting…</Text> : null}
    </Box>
  );
}

function OverlayView({ overlay }: { overlay: Overlay }) {
  return (
    <Box
      marginTop={1}
      borderStyle="round"
      borderColor="blue"
      flexDirection="column"
      paddingX={1}
    >
      <Text bold color="blue">
        {overlay.title}
      </Text>
      {overlay.tool ? (
        <ToolDetail entry={overlay.tool} />
      ) : overlay.lines.length === 0 ? (
        <Text dimColor>(empty)</Text>
      ) : (
        overlay.lines.map((line, index) => {
          const selected =
            overlay.sessions !== undefined &&
            (overlay.selected ?? 0) === index;
          return (
            <Text
              key={`overlay-${index}`}
              wrap="truncate"
              backgroundColor={selected ? "cyan" : undefined}
              color={selected ? "black" : undefined}
            >
              {overlay.sessions !== undefined
                ? selected
                  ? "❯ "
                  : "  "
                : ""}
              {line}
            </Text>
          );
        })
      )}
      <Text dimColor>
        {overlay.sessions
          ? "↑/↓ select · Enter open · Esc close"
          : "Esc close"}
      </Text>
    </Box>
  );
}

export function App({
  client,
  workspace,
  model,
  initialMode,
  initialEffort,
  initialSnapshot,
}: AppProps) {
  const { exit } = useApp();
  const [entries, setEntries] = useState<TimelineEntry[]>(
    initialSnapshot.entries ?? [],
  );
  const [phase, setPhase] = useState(initialSnapshot.phase ?? "idle");
  const [mode, setMode] = useState(
    initialSnapshot.approvalMode ?? initialMode,
  );
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequest[]>(
    initialSnapshot.pendingApprovals ?? [],
  );
  const [pendingQuestions, setPendingQuestions] = useState<QuestionRequest[]>(
    initialSnapshot.pendingQuestions ?? [],
  );
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>(
    initialSnapshot.queuedMessages ?? [],
  );
  const [backgroundTasks, setBackgroundTasks] = useState<BackgroundTask[]>(
    initialSnapshot.backgroundTasks ?? [],
  );
  const [readOnly, setReadOnly] = useState(
    initialSnapshot.readOnly ?? false,
  );
  const [error, setError] = useState<string | null>(
    initialSnapshot.error ?? null,
  );
  const [deltas, setDeltas] = useState<Map<string, string>>(new Map());
  const [thinking, setThinking] = useState<Map<string, string>>(new Map());
  const [showThinking, setShowThinking] = useState(false);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<Overlay | null>(null);
  const [staticEntries, setStaticEntries] = useState<TimelineEntry[]>(() => {
    const initial = initialSnapshot.entries ?? [];
    const held = heldAssistantStaticKey(initial);
    return initial.filter(
      (entry) =>
        isFinalEntry(entry) && staticEntryKey(entry) !== held,
    );
  });
  const [effort, setEffort] = useState(initialEffort);
  const [feedbackMode, setFeedbackMode] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [questionDraft, setQuestionDraft] = useState<QuestionDraft | null>(
    null,
  );
  const [questionBusy, setQuestionBusy] = useState(false);
  const [columns, setColumns] = useState(
    process.stdout.columns ?? 100,
  );
  const pollingRef = useRef(false);
  const indexBuildingRef = useRef(false);
  const emittedStaticRef = useRef<Set<string>>(
    (() => {
      const initial = initialSnapshot.entries ?? [];
      const held = heldAssistantStaticKey(initial);
      return new Set(
        initial
          .filter(
            (entry) =>
              isFinalEntry(entry) &&
              staticEntryKey(entry) !== held,
          )
          .map(staticEntryKey),
      );
    })(),
  );
  const sessionRef = useRef<string | undefined>(
    initialSnapshot.sessionId ?? undefined,
  );
  const [commandIndex, setCommandIndex] = useState(0);
  const heldStaticKey = useMemo(
    () => heldAssistantStaticKey(entries),
    [entries],
  );

  const commandQuery = input.startsWith("/") ? input.slice(1) : "";
  const commandMenuVisible =
    input.startsWith("/") &&
    !input.includes(" ") &&
    pendingApprovals.length === 0 &&
    pendingQuestions.length === 0 &&
    !feedbackMode &&
    !overlay;
  const commandMatches = useMemo(
    () =>
      commandMenuVisible
        ? COMMANDS.filter((command) =>
            command.name.startsWith(commandQuery.toLowerCase()),
          )
        : [],
    [commandMenuVisible, commandQuery],
  );

  useEffect(() => {
    setCommandIndex((current) =>
      commandMatches.length === 0
        ? 0
        : Math.min(current, commandMatches.length - 1),
    );
  }, [commandMatches.length]);

  const resetConversation = useCallback(() => {
    setStaticEntries([]);
    emittedStaticRef.current.clear();
    setDeltas(new Map());
    setThinking(new Map());
    setAttachments([]);
    setOverlay(null);
    setQuestionDraft(null);
  }, []);

  const applySnapshot = useCallback(
    (snapshot: SessionSnapshot) => {
      const nextSession = snapshot.sessionId ?? undefined;
      if (
        nextSession &&
        sessionRef.current &&
        nextSession !== sessionRef.current
      ) {
        resetConversation();
      }
      sessionRef.current = nextSession ?? sessionRef.current;
      setEntries(snapshot.entries ?? []);
      setPhase(snapshot.phase ?? "idle");
      setMode((current) => snapshot.approvalMode ?? current);
      setPendingApprovals(snapshot.pendingApprovals ?? []);
      setPendingQuestions(snapshot.pendingQuestions ?? []);
      setQueuedMessages(snapshot.queuedMessages ?? []);
      setBackgroundTasks(snapshot.backgroundTasks ?? []);
      setReadOnly(snapshot.readOnly ?? false);
      setError(snapshot.error ?? null);
    },
    [resetConversation],
  );

  useEffect(() => {
    const additions: TimelineEntry[] = [];
    for (const entry of entries) {
      if (!isFinalEntry(entry)) {
        continue;
      }
      const key = staticEntryKey(entry);
      if (key === heldStaticKey) {
        continue;
      }
      if (emittedStaticRef.current.has(key)) {
        continue;
      }
      emittedStaticRef.current.add(key);
      additions.push(entry);
    }
    if (additions.length > 0) {
      setStaticEntries((current) => [...current, ...additions]);
    }
  }, [entries, heldStaticKey]);

  useEffect(() => {
    const unsubscribe = client.onEvent((event: UiEvent) => {
      switch (event.type) {
        case "session.snapshot": {
          if (event.snapshot) {
            applySnapshot(event.snapshot);
          }
          return;
        }
        case "message.delta": {
          const id = String(event.messageId ?? "");
          const delta = String(event.delta ?? "");
          if (!id || !delta) {
            return;
          }
          setDeltas((current) => {
            const next = new Map(current);
            next.set(id, (next.get(id) ?? "") + delta);
            return next;
          });
          return;
        }
        case "thinking.delta": {
          const id = String(event.messageId ?? "");
          const delta = String(event.delta ?? "");
          if (!id || !delta) {
            return;
          }
          setThinking((current) => {
            const next = new Map(current);
            next.set(id, (next.get(id) ?? "") + delta);
            return next;
          });
          return;
        }
        case "thinking.completed":
          return;
        case "session.queue": {
          const queued = event.queuedMessages;
          setQueuedMessages(Array.isArray(queued) ? queued : []);
          return;
        }
        case "background.updated": {
          const task = event.task as BackgroundTask | undefined;
          if (!task?.id) {
            return;
          }
          setBackgroundTasks((current) => {
            const index = current.findIndex((item) => item.id === task.id);
            if (index === -1) {
              return [...current, task];
            }
            const next = [...current];
            next[index] = task;
            return next;
          });
          if (
            task.status === "completed" ||
            task.status === "failed" ||
            task.status === "cancelled"
          ) {
            setNotice(
              `Background task ${task.id} ${task.status}: ${
                task.description ?? ""
              }`,
            );
          }
          return;
        }
        case "question.requested": {
          const request = event.question as QuestionRequest | undefined;
          if (!request) {
            return;
          }
          setPendingQuestions((current) =>
            current.some((item) => item.callId === request.callId)
              ? current
              : [...current, request],
          );
          return;
        }
        case "question.resolved": {
          const callId = String(event.callId ?? "");
          setPendingQuestions((current) =>
            current.filter((item) => item.callId !== callId),
          );
          setQuestionDraft((current) =>
            current?.callId === callId ? null : current,
          );
          return;
        }
        case "run.failed": {
          setError(String(event.message ?? "Run failed."));
          setPhase("failed");
          return;
        }
        case "run.cancelled": {
          setPhase("cancelled");
          return;
        }
        case "plan.created": {
          const path = String(event.path ?? "");
          setNotice(path ? `Plan written to ${path}` : "Plan created.");
          return;
        }
        default:
          return;
      }
    });
    return unsubscribe;
  }, [applySnapshot, client]);

  useEffect(() => {
    let alive = true;
    const timer = setInterval(() => {
      if (pollingRef.current) {
        return;
      }
      pollingRef.current = true;
      void client
        .call<SessionSnapshot>("session.snapshot", {}, 15_000)
        .then((snapshot) => {
          if (alive) {
            applySnapshot(snapshot);
          }
        })
        .catch(() => undefined)
        .finally(() => {
          pollingRef.current = false;
        });
    }, 1_500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [applySnapshot, client]);

  useEffect(() => {
    const onExit = () => {
      setError("Ling sidecar exited. Press Ctrl+C to quit.");
      setPhase("failed");
    };
    client.on("exit", onExit);
    return () => {
      client.off("exit", onExit);
    };
  }, [client]);

  useEffect(() => {
    const onResize = () => {
      setColumns(process.stdout.columns ?? 100);
    };
    process.stdout.on("resize", onResize);
    return () => {
      process.stdout.off("resize", onResize);
    };
  }, []);

  useEffect(() => {
    if (!process.stdout.isTTY) {
      return;
    }
    process.stdout.write(HIDE_CURSOR);
    return () => {
      process.stdout.write(SHOW_CURSOR);
    };
  }, []);

  useEffect(() => {
    const request = pendingQuestions[0];
    if (!request) {
      setQuestionDraft((current) => (current ? null : current));
      return;
    }
    setQuestionDraft((current) =>
      current?.callId === request.callId
        ? current
        : {
            callId: request.callId,
            index: 0,
            selected: {},
            customs: {},
            customMode: false,
            customInput: "",
          },
    );
  }, [pendingQuestions]);

  useEffect(() => {
    setOverlay((current) =>
      current?.title === "Background tasks"
        ? {
            title: "Background tasks",
            lines: taskLines(backgroundTasks),
          }
        : current,
    );
  }, [backgroundTasks]);

  useEffect(() => {
    const listener = (params: unknown) => {
      if (!indexBuildingRef.current) {
        return;
      }
      const status =
        params && typeof params === "object"
          ? (params as Record<string, unknown>)
          : {};
      setOverlay((current) =>
        current?.title === "Code index"
          ? {
              title: "Code index",
              lines: ["Building…", ...indexStatusLines(status)],
            }
          : current,
      );
    };
    client.on("index-progress", listener);
    return () => {
      client.off("index-progress", listener);
    };
  }, [client]);

  const busy =
    phase === "running" ||
    phase === "waiting_approval" ||
    pendingApprovals.length > 0 ||
    pendingQuestions.length > 0;

  const sendPrompt = useCallback(
    async (prompt: string, sending: PendingAttachment[]) => {
      if (readOnly) {
        setNotice("This session is read-only. Open a writable session first.");
        return;
      }
      setNotice(null);
      setOverlay(null);
      try {
        await client.call<JsonObject>(
          "session.start",
          {
            prompt,
            workspaceRoot: workspace,
            approvalMode: mode,
            reasoning: effort,
            attachments: sending,
            selections: [],
            activePaths: [],
          },
          30_000,
        );
        setAttachments((current) =>
          current.filter((item) => !sending.includes(item)),
        );
      } catch (cause) {
        setNotice(
          `Send failed: ${
            cause instanceof Error ? cause.message : String(cause)
          }`,
        );
      }
    },
    [client, effort, mode, readOnly, workspace],
  );

  const cycleMode = useCallback(async () => {
    if (busy) {
      setNotice("Cancel the current run before switching approval mode.");
      return;
    }
    const current = MODES.indexOf(mode as (typeof MODES)[number]);
    const next = MODES[(current + 1) % MODES.length];
    try {
      const result = await client.call<{ mode?: string }>(
        "session.setMode",
        { mode: next },
        20_000,
      );
      setMode(result.mode ?? next);
      setNotice(`Approval mode: ${result.mode ?? next}`);
    } catch (cause) {
      setNotice(
        `Mode change failed: ${
          cause instanceof Error ? cause.message : String(cause)
        }`,
      );
    }
  }, [busy, client, mode]);

  const interruptRun = useCallback(async () => {
    const queued = queuedMessages[queuedMessages.length - 1];
    if (!busy && !queued) {
      return;
    }
    try {
      if (queued) {
        setNotice("Interrupting and promoting the newest queued message…");
        await client.call(
          "session.queued.sendNow",
          { queuedId: queued.id },
          20_000,
        );
      } else {
        setNotice("Interrupting the current run…");
        await client.call("session.cancel", {}, 60_000);
      }
    } catch (cause) {
      setNotice(
        `Interrupt failed: ${
          cause instanceof Error ? cause.message : String(cause)
        }`,
      );
    }
  }, [busy, client, queuedMessages]);

  const fetchHistory = useCallback(
    async (query: string) => {
      const result = query
        ? await client.call<{ sessions?: HistorySession[] }>(
            "session.search",
            { query, workspaceRoot: workspace },
            20_000,
          )
        : await client.call<{ sessions?: HistorySession[] }>(
            "session.list",
            { workspaceRoot: workspace },
            20_000,
          );
      return result.sessions ?? [];
    },
    [client, workspace],
  );

  const openSession = useCallback(
    async (session: HistorySession) => {
      try {
        const snapshot = await client.call<SessionSnapshot>(
          "session.open",
          {
            sessionId: session.id,
            workspaceRoot: workspace,
          },
          20_000,
        );
        resetConversation();
        applySnapshot(snapshot);
        setOverlay(null);
        setNotice(`Opened ${shortId(session.id)}`);
      } catch (cause) {
        setNotice(
          `Open failed: ${
            cause instanceof Error ? cause.message : String(cause)
          }`,
        );
      }
    },
    [applySnapshot, client, resetConversation, workspace],
  );

  const submitQuestionAnswers = useCallback(
    async (request: QuestionRequest, draft: QuestionDraft) => {
      if (questionBusy) {
        return;
      }
      setQuestionBusy(true);
      try {
        const answers = request.questions.map((question) => {
          const selected = draft.selected[question.id] ?? [];
          const custom = draft.customs[question.id];
          return {
            id: question.id,
            selected,
            ...(custom ? { custom } : {}),
          };
        });
        const result = await client.call<{ resolved?: boolean }>(
          "question.answer",
          { callId: request.callId, answers },
          20_000,
        );
        if (!result.resolved) {
          setNotice("Question expired or was already answered.");
        }
        setQuestionDraft(null);
        setInput("");
      } catch (cause) {
        setNotice(
          `Answer failed: ${
            cause instanceof Error ? cause.message : String(cause)
          }`,
        );
      } finally {
        setQuestionBusy(false);
      }
    },
    [client, questionBusy],
  );

  const advanceQuestion = useCallback(
    (request: QuestionRequest, draft: QuestionDraft) => {
      const nextDraft: QuestionDraft = {
        ...draft,
        customMode: false,
        customInput: "",
      };
      if (draft.index + 1 < request.questions.length) {
        setQuestionDraft({ ...nextDraft, index: draft.index + 1 });
      } else {
        void submitQuestionAnswers(request, nextDraft);
      }
    },
    [submitQuestionAnswers],
  );

  const resolveApproval = useCallback(
    async (
      approved: boolean,
      options?: {
        remember?: boolean;
        feedback?: string;
        mode?: string;
      },
    ) => {
      const approval = pendingApprovals[0];
      if (!approval || approvalBusy) {
        return;
      }
      setApprovalBusy(true);
      try {
        if (options?.mode) {
          await client.call(
            "session.setMode",
            { mode: options.mode },
            20_000,
          );
          setMode(options.mode);
        }
        const result = await client.call<{ resolved?: boolean }>(
          "approval.resolve",
          {
            approvalId: approval.id,
            effectDigest: approval.effectDigest,
            approved,
            remember: options?.remember ?? false,
            feedback: options?.feedback ?? null,
          },
          20_000,
        );
        if (!result.resolved) {
          setNotice("Approval expired or was already resolved.");
        }
        setOverlay(null);
      } catch (cause) {
        setNotice(
          `Approval failed: ${
            cause instanceof Error ? cause.message : String(cause)
          }`,
        );
      } finally {
        setApprovalBusy(false);
        setFeedbackMode(false);
        setFeedback("");
      }
    },
    [approvalBusy, client, pendingApprovals],
  );

  const runCommand = useCallback(
    async (command: string) => {
      const [name, ...rest] = command.slice(1).split(/\s+/);
      const argument = rest.join(" ").trim();
      switch (name) {
        case "help":
          setOverlay({ title: "Help", lines: HELP.split("\n") });
          return;
        case "new":
          try {
            await client.call("session.new", {}, 20_000);
            resetConversation();
            setEntries([]);
            setPendingApprovals([]);
            setPendingQuestions([]);
            setQueuedMessages([]);
            setBackgroundTasks([]);
            setMode("manual");
            setReadOnly(false);
            setNotice("New session.");
          } catch (cause) {
            setNotice(
              `New session failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        case "cancel":
          try {
            await client.call("session.cancel", {}, 60_000);
            setNotice("Cancellation requested.");
          } catch (cause) {
            setNotice(
              `Cancel failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        case "history": {
          try {
            const sessions = await fetchHistory(argument);
            if (sessions.length === 0) {
              setNotice(argument ? "No matching sessions." : "No sessions.");
              return;
            }
            const visible = sessions.slice(0, 20);
            setOverlay({
              title: argument
                ? `History · ${argument}`
                : "History · this workspace",
              sessions: visible,
              selected: 0,
              lines: visible.map(
                (session) =>
                  `${shortId(session.id)}  [${session.status ?? "?"}]  ${truncate(session.title, 100)}  ${formatTime(session.updatedAt)}`,
              ),
            });
          } catch (cause) {
            setNotice(
              `History failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "tasks": {
          try {
            const result = await client.call<{
              tasks?: BackgroundTask[];
            }>("background.list", {}, 20_000);
            const tasks = result.tasks ?? [];
            setBackgroundTasks(tasks);
            if (tasks.length === 0) {
              setNotice("No background tasks.");
              return;
            }
            setOverlay({
              title: "Background tasks",
              lines: taskLines(tasks),
            });
          } catch (cause) {
            setNotice(
              `Tasks failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "stop-task": {
          if (!argument) {
            setNotice("Usage: /stop-task <taskId>");
            return;
          }
          const task = backgroundTasks.find(
            (candidate) =>
              candidate.id === argument ||
              candidate.id.startsWith(argument),
          );
          if (!task) {
            setNotice(`No background task matches: ${argument}`);
            return;
          }
          try {
            await client.call(
              "background.stop",
              { taskId: task.id },
              20_000,
            );
            setOverlay(null);
            setNotice(`Stopping ${shortId(task.id)}`);
          } catch (cause) {
            setNotice(
              `Stop-task failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "details": {
          const recent = [...entries]
            .reverse()
            .filter((candidate) => Boolean(candidate.tool?.id));
          if (recent.length === 0) {
            setNotice("No tool details available.");
            return;
          }
          let entry: TimelineEntry | undefined;
          if (argument && /^\d+$/.test(argument)) {
            const position = Number.parseInt(argument, 10);
            if (position < 1 || position > recent.length) {
              setNotice(
                `Tool index must be between 1 and ${recent.length}.`,
              );
              return;
            }
            entry = recent[position - 1];
          } else if (argument) {
            entry = entries.find(
              (candidate) =>
                candidate.tool?.id === argument ||
                candidate.tool?.id?.startsWith(argument),
            );
          } else {
            entry = recent[0];
          }
          if (!entry?.tool) {
            setNotice(`No tool matches: ${argument}`);
            return;
          }
          setOverlay({
            title: toolTitle(entry),
            lines: [],
            tool: entry,
          });
          return;
        }
        case "last": {
          const entry = [...entries]
            .reverse()
            .find((candidate) => Boolean(candidate.tool?.id));
          if (!entry?.tool) {
            setNotice("No tool details available.");
            return;
          }
          setOverlay({
            title: toolTitle(entry),
            lines: [],
            tool: entry,
          });
          return;
        }
        case "attach": {
          if (!argument) {
            setNotice("Usage: /attach <path>");
            return;
          }
          if (attachments.length >= MAX_PENDING_ATTACHMENTS) {
            setNotice(
              `At most ${MAX_PENDING_ATTACHMENTS} attachments per message.`,
            );
            return;
          }
          try {
            const attachment = await prepareAttachment(
              argument,
              workspace,
            );
            setAttachments((current) => [...current, attachment]);
            setNotice(
              `Attached ${attachment.name} (${attachment.kind}). Send a message to include it.`,
            );
          } catch (cause) {
            setNotice(
              `Attach failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "detach": {
          setAttachments([]);
          setNotice("Pending attachments cleared.");
          return;
        }
        case "mcp": {
          try {
            const result = await client.call<Record<string, unknown>>(
              "mcp.status",
              { workspaceRoot: workspace },
              60_000,
            );
            const servers = Array.isArray(result.servers)
              ? (result.servers as Array<Record<string, unknown>>)
              : [];
            const issues = Array.isArray(result.issues)
              ? (result.issues as Array<Record<string, unknown>>)
              : [];
            const lines = [
              `config ${String(result.configPath ?? "?")}`,
            ];
            if (servers.length === 0) {
              lines.push("No MCP servers configured.");
            }
            for (const server of servers) {
              const name = String(server.name ?? "?");
              const kind = String(server.kind ?? "?");
              const state = String(server.state ?? "?");
              const status =
                server.enabled === false ? "disabled" : "enabled";
              const tools = Number(server.toolCount ?? 0);
              const auth =
                server.auth === "oauth"
                  ? ` · oauth${
                      server.authorized === false
                        ? " (not authorized)"
                        : ""
                    }`
                  : "";
              lines.push(
                `${name} · ${kind} · ${state} · ${status} · ${tools} tools${auth}`,
              );
              if (server.target) {
                lines.push(
                  `  ${truncate(String(server.target), 100)}`,
                );
              }
              if (server.error) {
                lines.push(
                  `  error: ${truncate(String(server.error), 120)}`,
                );
              }
            }
            if (issues.length > 0) {
              lines.push("", "Issues:");
              for (const issue of issues) {
                lines.push(
                  `  ${String(issue.name ?? "?")}: ${truncate(
                    String(issue.message ?? ""),
                    120,
                  )}`,
                );
              }
            }
            setOverlay({ title: "MCP status", lines });
          } catch (cause) {
            setNotice(
              `MCP status failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "index": {
          indexBuildingRef.current = true;
          setOverlay({
            title: "Code index",
            lines: ["Building…"],
          });
          setNotice("Building code index…");
          try {
            const status = await client.call<Record<string, unknown>>(
              "index.build",
              { workspaceRoot: workspace },
              30 * 60 * 1000,
            );
            setOverlay({
              title: "Code index",
              lines: indexStatusLines(status),
            });
            setNotice("Code index updated.");
          } catch (cause) {
            setNotice(
              `Index build failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          } finally {
            indexBuildingRef.current = false;
          }
          return;
        }
        case "index-status": {
          try {
            const status = await client.call<Record<string, unknown>>(
              "index.status",
              { workspaceRoot: workspace },
              30_000,
            );
            setOverlay({
              title: "Code index",
              lines: indexStatusLines(status),
            });
          } catch (cause) {
            setNotice(
              `Index status failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "changes": {
          const sessionId = sessionRef.current;
          if (!sessionId) {
            setNotice("No active session to inspect.");
            return;
          }
          const runId =
            argument ||
            [...entries]
              .reverse()
              .find((entry) => entry.runId)?.runId;
          if (!runId) {
            setNotice("No recorded run to inspect.");
            return;
          }
          try {
            const result = await client.call<Record<string, unknown>>(
              "changes.get",
              { sessionId, runId },
              30_000,
            );
            const changes = Array.isArray(result.changes)
              ? (result.changes as Array<Record<string, unknown>>)
              : [];
            if (changes.length === 0) {
              setNotice("No recorded changes for this run.");
              return;
            }
            const lines = changes.map((change) => {
              const flags = [
                change.beforeExists === false ? "new" : "",
                change.afterExists === false ? "deleted" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return `${String(change.path ?? "?")}  +${Number(
                change.added ?? 0,
              )} −${Number(change.removed ?? 0)}${
                flags ? `  (${flags})` : ""
              }`;
            });
            setOverlay({
              title: `Changes · ${changes.length} file${
                changes.length === 1 ? "" : "s"
              }`,
              lines,
            });
          } catch (cause) {
            setNotice(
              `Changes failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "mode": {
          if (!argument) {
            setNotice(`Approval mode: ${mode}`);
            return;
          }
          if (
            !["manual", "accept-write", "auto", "plan"].includes(argument)
          ) {
            setNotice("Mode must be manual, accept-write, auto, or plan.");
            return;
          }
          try {
            const result = await client.call<{ mode?: string }>(
              "session.setMode",
              { mode: argument },
              20_000,
            );
            setMode(result.mode ?? argument);
            setNotice(`Approval mode: ${result.mode ?? argument}`);
          } catch (cause) {
            setNotice(
              `Mode change failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "effort": {
          if (!argument) {
            setNotice(`Reasoning effort: ${effort}`);
            return;
          }
          if (
            !["low", "medium", "high", "xhigh", "max"].includes(argument)
          ) {
            setNotice("Effort must be low, medium, high, xhigh, or max.");
            return;
          }
          setEffort(argument);
          setNotice(`Reasoning effort: ${argument}`);
          return;
        }
        case "status": {
          try {
            const snapshot = await client.call<SessionSnapshot>(
              "session.snapshot",
              {},
              15_000,
            );
            applySnapshot(snapshot);
            setNotice(
              `phase=${snapshot.phase} mode=${
                snapshot.approvalMode ?? mode
              } entries=${snapshot.entries?.length ?? 0} approvals=${
                snapshot.pendingApprovals?.length ?? 0
              } questions=${snapshot.pendingQuestions?.length ?? 0} queue=${
                snapshot.queuedMessages?.length ?? 0
              } background=${snapshot.backgroundTasks?.length ?? 0}`,
            );
          } catch (cause) {
            setNotice(
              `Status failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            );
          }
          return;
        }
        case "quit":
        case "exit":
          exit();
          return;
        default:
          setNotice(`Unknown command: /${name}. Try /help.`);
      }
    },
    [
      applySnapshot,
      attachments,
      backgroundTasks,
      client,
      effort,
      entries,
      exit,
      fetchHistory,
      mode,
      openSession,
      resetConversation,
      workspace,
    ],
  );

  const submit = useCallback(() => {
    const text = input.trim();
    if (!text && attachments.length === 0) {
      return;
    }
    setInput("");
    if (text.startsWith("/")) {
      void runCommand(text);
      return;
    }
    void sendPrompt(text, attachments);
  }, [attachments, input, runCommand, sendPrompt]);

  useInput((rawInput, key) => {
    if (key.ctrl && rawInput === "c") {
      if (busy) {
        void client
          .call("session.cancel", {}, 60_000)
          .then(() => setNotice("Cancellation requested."))
          .catch((cause) =>
            setNotice(
              `Cancel failed: ${
                cause instanceof Error ? cause.message : String(cause)
              }`,
            ),
          );
      } else {
        exit();
      }
      return;
    }
    if (key.ctrl && rawInput === "d") {
      if (!input) {
        exit();
      }
      return;
    }
    if (key.ctrl && key.meta && rawInput === "t") {
      const next = !showThinking;
      setShowThinking(next);
      setNotice(
        next
          ? "Thinking expanded. Ctrl+Alt+T to collapse."
          : "Thinking collapsed. Ctrl+Alt+T to expand.",
      );
      return;
    }
    if (
      (key.tab && key.shift) ||
      rawInput === SHIFT_TAB
    ) {
      if (pendingApprovals.length === 0 && pendingQuestions.length === 0) {
        void cycleMode();
      }
      return;
    }
    if (key.escape) {
      if (overlay) {
        setOverlay(null);
        return;
      }
      if (commandMenuVisible) {
        setInput("");
        return;
      }
      if (questionDraft?.customMode) {
        setQuestionDraft({
          ...questionDraft,
          customMode: false,
          customInput: "",
        });
        return;
      }
      if (feedbackMode) {
        setFeedbackMode(false);
        setFeedback("");
        return;
      }
      if (busy || queuedMessages.length > 0) {
        void interruptRun();
        return;
      }
      setNotice(null);
      return;
    }
    if (overlay?.sessions) {
      const count = overlay.sessions.length;
      if (key.upArrow) {
        setOverlay((current) =>
          current?.sessions
            ? {
                ...current,
                selected:
                  ((current.selected ?? 0) - 1 + count) % count,
              }
            : current,
        );
        return;
      }
      if (key.downArrow) {
        setOverlay((current) =>
          current?.sessions
            ? {
                ...current,
                selected: ((current.selected ?? 0) + 1) % count,
              }
            : current,
        );
        return;
      }
      if (key.return) {
        const session =
          overlay.sessions[overlay.selected ?? 0];
        if (session) {
          setOverlay(null);
          void openSession(session);
        }
        return;
      }
      return;
    }
    if (pendingApprovals.length > 0) {
      const approval = pendingApprovals[0];
      if (feedbackMode) {
        if (key.return) {
          void resolveApproval(false, { feedback });
        } else if (key.backspace || key.delete) {
          setFeedback((current) => current.slice(0, -1));
        } else if (rawInput && !key.ctrl && !key.meta) {
          setFeedback((current) => current + rawInput);
        }
        return;
      }
      if (rawInput === "1") {
        void resolveApproval(true);
        return;
      }
      if (rawInput === "2" && approval.allowAlways) {
        if (approval.allowAlways.kind === "mode" && approval.allowAlways.mode) {
          void resolveApproval(true, { mode: approval.allowAlways.mode });
        } else {
          void resolveApproval(true, { remember: true });
        }
        return;
      }
      if (rawInput === "3") {
        void resolveApproval(false);
        return;
      }
      if (rawInput === "4") {
        setFeedbackMode(true);
        setFeedback("");
        return;
      }
      return;
    }

    if (pendingQuestions.length > 0 && questionDraft) {
      const request = pendingQuestions[0];
      const question = currentQuestion(request, questionDraft);
      if (!question) {
        return;
      }
      const options = question.options ?? [];
      if (questionDraft.customMode || options.length === 0) {
        if (key.return) {
          const value = questionDraft.customInput.trim();
          if (!value) {
            return;
          }
          const customs = {
            ...questionDraft.customs,
            [question.id]: value,
          };
          const selected = question.multiSelect
            ? questionDraft.selected
            : { ...questionDraft.selected, [question.id]: [] };
          advanceQuestion(request, {
            ...questionDraft,
            selected,
            customs,
          });
          return;
        }
        if (key.backspace || key.delete) {
          setQuestionDraft({
            ...questionDraft,
            customInput: questionDraft.customInput.slice(0, -1),
          });
          return;
        }
        if (rawInput && !key.ctrl && !key.meta) {
          setQuestionDraft({
            ...questionDraft,
            customInput: questionDraft.customInput + rawInput,
          });
        }
        return;
      }
      if (rawInput === "c") {
        setQuestionDraft({
          ...questionDraft,
          customMode: true,
          customInput: "",
        });
        return;
      }
      const index = Number.parseInt(rawInput, 10);
      if (!Number.isNaN(index) && index >= 1 && index <= options.length) {
        const label = options[index - 1].label;
        if (question.multiSelect) {
          const selected = new Set(
            questionDraft.selected[question.id] ?? [],
          );
          if (selected.has(label)) {
            selected.delete(label);
          } else {
            selected.add(label);
          }
          setQuestionDraft({
            ...questionDraft,
            selected: {
              ...questionDraft.selected,
              [question.id]: [...selected],
            },
          });
        } else {
          const selected = {
            ...questionDraft.selected,
            [question.id]: [label],
          };
          advanceQuestion(request, {
            ...questionDraft,
            selected,
          });
        }
        return;
      }
      if (key.return && question.multiSelect) {
        advanceQuestion(request, questionDraft);
        return;
      }
      return;
    }

    if (commandMatches.length > 0) {
      if (key.upArrow) {
        setCommandIndex((current) =>
          (current - 1 + commandMatches.length) % commandMatches.length,
        );
        return;
      }
      if (key.downArrow) {
        setCommandIndex(
          (current) => (current + 1) % commandMatches.length,
        );
        return;
      }
      const selected = commandMatches[commandIndex] ?? commandMatches[0];
      if (key.tab && !key.shift) {
        setInput(`/${selected.name} `);
        return;
      }
      if (key.return) {
        setInput("");
        void runCommand(`/${selected.name}`);
        return;
      }
    }

    if (key.return) {
      submit();
      return;
    }
    if (key.backspace || key.delete) {
      setInput((current) => current.slice(0, -1));
      return;
    }
    if (rawInput && !key.ctrl && !key.meta && rawInput !== "\t") {
      setInput((current) => current + rawInput);
    }
  });

  const dynamicEntries = entries.filter(
    (entry) =>
      !isFinalEntry(entry) ||
      (heldStaticKey !== null &&
        staticEntryKey(entry) === heldStaticKey),
  );
  const turnGroups = useMemo(() => {
    const runsWithTools = new Set<string>();
    for (const entry of entries) {
      if (entry.kind === "tool" && entry.runId) {
        runsWithTools.add(entry.runId);
      }
    }
    const headers = new Set<TimelineEntry>();
    const seen = new Set<string>();
    for (const entry of [...staticEntries, ...dynamicEntries]) {
      const runId = entry.runId;
      if (!runId || !runsWithTools.has(runId)) {
        continue;
      }
      if (entry.kind === "message" && entry.message?.role === "user") {
        continue;
      }
      if (
        entry.kind === "message" &&
        entry.message?.role === "assistant" &&
        !messageText(entry.message.content)
      ) {
        continue;
      }
      if (
        !["message", "tool", "approval", "compaction"].includes(entry.kind)
      ) {
        continue;
      }
      if (seen.has(runId)) {
        continue;
      }
      seen.add(runId);
      headers.add(entry);
    }
    return { runsWithTools, headers };
  }, [entries, staticEntries, dynamicEntries]);
  const latestAssistant = useMemo(() => {
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const entry = entries[index];
      if (entry.kind === "message" && entry.message?.role === "assistant") {
        return entry;
      }
    }
    return null;
  }, [entries]);
  const latestRunThinking = useMemo(() => {
    const runId = latestAssistant?.runId;
    if (!runId) {
      return "";
    }
    const parts: string[] = [];
    for (const entry of entries) {
      if (
        entry.kind !== "message" ||
        entry.message?.role !== "assistant" ||
        entry.runId !== runId
      ) {
        continue;
      }
      const id = entry.message.id ?? "";
      const live = thinking.get(id) ?? "";
      const stored = entry.message.thinking ?? "";
      const text = live.length >= stored.length ? live : stored;
      if (text.trim()) {
        parts.push(text.trim());
      }
    }
    return parts.join("\n\n");
  }, [entries, latestAssistant, thinking]);
  const entryIds = useMemo(
    () =>
      new Set(
        entries
          .map((entry) => entry.message?.id)
          .filter((id): id is string => Boolean(id)),
      ),
    [entries],
  );
  const orphanStream = useMemo(
    () =>
      [...deltas.entries()]
        .filter(([id]) => !entryIds.has(id))
        .map(([, text]) => text)
        .join(""),
    [deltas, entryIds],
  );
  const workspaceLabel =
    workspace.length > 42 ? `…${workspace.slice(-41)}` : workspace;
  const terminalWidth = Math.max(30, columns - 4);
  const contentWidth = Math.max(20, columns - 8);
  const modeColor = MODE_COLORS[mode] ?? "cyan";
  const activeBackground = backgroundTasks.filter(
    (task) => task.status === "pending" || task.status === "running",
  ).length;
  const statusDetails = [
    workspaceLabel,
    model,
    phase,
    ...(readOnly ? ["read-only"] : []),
    ...(attachments.length > 0
      ? [`attach ${attachments.length}`]
      : []),
    ...(queuedMessages.length > 0
      ? [`queue ${queuedMessages.length}`]
      : []),
    ...(activeBackground > 0 ? [`bg ${activeBackground}`] : []),
    ...(pendingApprovals.length > 0
      ? [`approvals ${pendingApprovals.length}`]
      : []),
    ...(pendingQuestions.length > 0
      ? [`questions ${pendingQuestions.length}`]
      : []),
  ].join(" · ");
  const statusRest = clip(
    ` · ${statusDetails}`,
    Math.max(1, contentWidth - mode.length),
  );
  const displayInput = feedbackMode
    ? feedback
    : questionDraft?.customMode
      ? questionDraft.customInput
      : questionDraft && pendingQuestions.length > 0 &&
          (currentQuestion(pendingQuestions[0], questionDraft)?.options
            ?.length ?? 0) === 0
        ? questionDraft.customInput
        : input;
  const inputLabel = feedbackMode
    ? "feedback"
    : questionDraft?.customMode
      ? "custom"
      : pendingApprovals.length > 0
        ? "approval"
        : pendingQuestions.length > 0
          ? "answer"
          : "message";
  const inputText = clip(
    displayInput,
    Math.max(4, contentWidth - inputLabel.length - 4),
  );
  const footerText = clip(
    `Enter send · Shift+Tab mode · Ctrl+Alt+T thinking · Esc ${
      busy || queuedMessages.length > 0 ? "interrupt" : "close"
    } · Ctrl+C cancel/exit · /help`,
    terminalWidth,
  );

  return (
    <Box flexDirection="column" paddingX={1}>
      <Static items={staticEntries}>
        {(entry) => (
          <EntryView
            key={staticEntryKey(entry)}
            entry={entry}
            deltas={deltas}
            thinking={thinking}
            phase={phase}
            width={contentWidth}
            nested={
              Boolean(entry.runId) &&
              turnGroups.runsWithTools.has(entry.runId ?? "")
            }
            turnHeader={turnGroups.headers.has(entry)}
            showThinking={showThinking}
            turnThinking={
              entry === latestAssistant ? latestRunThinking : undefined
            }
          />
        )}
      </Static>

      {dynamicEntries.map((entry, index) => (
        <EntryView
          key={entryKey(entry, index)}
          entry={entry}
          deltas={deltas}
          thinking={thinking}
          phase={phase}
          width={contentWidth}
          nested={
            Boolean(entry.runId) &&
            turnGroups.runsWithTools.has(entry.runId ?? "")
          }
          turnHeader={turnGroups.headers.has(entry)}
          showThinking={showThinking}
          turnThinking={
            entry === latestAssistant ? latestRunThinking : undefined
          }
        />
      ))}

      {orphanStream ? (
        <Box marginTop={1} flexDirection="row">
          <Text color="cyan" bold>
            Ling:
          </Text>
          <Box
            flexDirection="column"
            flexGrow={1}
            flexShrink={1}
            paddingLeft={1}
          >
            <Markdown text={orphanStream} width={contentWidth} />
          </Box>
        </Box>
      ) : null}

      {error ? (
        <Box marginTop={1}>
          <Text color="red">{error}</Text>
        </Box>
      ) : null}
      {notice ? (
        <Box marginTop={1}>
          <Text dimColor>{notice}</Text>
        </Box>
      ) : null}

      {queuedMessages.length > 0 ? (
        <Box marginTop={1} flexDirection="column">
          <Text bold color="yellow">
            Queued · {queuedMessages.length}
          </Text>
          {[...queuedMessages].reverse().map((item) => (
            <Text key={item.id} color="yellow" wrap="truncate">
              {"  ⏳ "}
              {truncate(item.text, 100)}
            </Text>
          ))}
          <Text dimColor>
            Esc interrupts the current run and promotes the top queued
            message.
          </Text>
        </Box>
      ) : null}

      {overlay ? <OverlayView overlay={overlay} /> : null}

      {pendingApprovals.length > 0 ? (
        <ApprovalCard
          approval={pendingApprovals[0]}
          feedbackMode={feedbackMode}
          feedback={feedback}
        />
      ) : null}
      {pendingApprovals.length === 0 &&
      pendingQuestions.length > 0 &&
      questionDraft ? (
        <QuestionCard
          request={pendingQuestions[0]}
          draft={questionDraft}
          busy={questionBusy}
        />
      ) : null}

      {commandMenuVisible && commandMatches.length > 0 ? (
        <Box
          marginTop={1}
          borderStyle="round"
          borderColor="gray"
          flexDirection="column"
          paddingX={1}
        >
          {commandMatches.map((command, index) => {
            const selected = index === commandIndex;
            return (
              <Text
                key={command.name}
                backgroundColor={selected ? "cyan" : undefined}
                wrap="truncate"
              >
                <Text color={selected ? "black" : "cyan"} bold>
                  {`/${command.name}`.padEnd(16)}
                </Text>
                <Text color={selected ? "black" : "gray"}>
                  {command.description}
                </Text>
              </Text>
            );
          })}
          <Text dimColor wrap="truncate">
            ↑/↓ select · Tab complete · Enter run · Esc close
          </Text>
        </Box>
      ) : null}

      <Box
        marginTop={1}
        flexDirection="column"
        borderStyle="round"
        borderColor={modeColor}
        paddingX={1}
      >
        <Text wrap="truncate">
          <Text bold color={modeColor}>
            {mode}
          </Text>
          <Text dimColor>{statusRest}</Text>
        </Text>
        {attachments.length > 0 ? (
          <Text color="cyan" wrap="truncate">
            Attached: {attachments.map((item) => item.name).join(", ")}
          </Text>
        ) : null}
        <Box>
          <Text bold color={modeColor}>
            {inputLabel}:{" "}
          </Text>
          <Text wrap="truncate">{inputText}</Text>
          <Text color="cyan">▌</Text>
        </Box>
      </Box>
      <Text dimColor wrap="truncate">
        {footerText}
      </Text>
    </Box>
  );
}

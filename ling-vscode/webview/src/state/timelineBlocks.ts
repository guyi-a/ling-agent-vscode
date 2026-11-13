import type {
  CompactionItem,
  TimelineEntry,
  TimelineMessage,
  TimelineRunView,
  ToolCall,
  WorkspaceChangeSummary,
} from "../types";

export type TimelineContentBlock =
  | {
      kind: "entry";
      entry: Extract<TimelineEntry, { kind: "message" }>;
    }
  | { kind: "tools"; tools: ToolCall[]; runId?: string }
  | {
      kind: "tool-group";
      category: ToolGroupCategory;
      tools: ToolCall[];
      summary: string;
      failedCount: number;
      cancelledCount: number;
      runId?: string;
    }
  | { kind: "compaction"; compaction: CompactionItem }
  | {
      kind: "changes";
      runId: string;
      changes: WorkspaceChangeSummary[];
      reverted: boolean;
    }
  | {
      kind: "run-thinking";
      id: string;
      runId?: string;
      thinking: string;
      active: boolean;
      durationMs?: number;
    };

export type ToolGroupCategory =
  | "explore"
  | "edit"
  | "run"
  | "browser"
  | "web"
  | "collab"
  | "flow"
  | "memory"
  | "mcp"
  | "other";

export function groupTimelineEntries(
  entries: TimelineEntry[],
  runs: TimelineRunView[] = [],
): TimelineBlock[] {
  const completedRuns = new Map(
    runs
      .filter((run) => run.status === "completed")
      .map((run) => [run.id, run]),
  );
  const handledRuns = new Set<string>();
  const blocks: TimelineBlock[] = [];
  let looseEntries: TimelineEntry[] = [];

  const flushLooseEntries = () => {
    if (looseEntries.length > 0) {
      blocks.push(...groupContentEntries(looseEntries, false));
      looseEntries = [];
    }
  };

  for (const entry of entries) {
    const run = entry.runId
      ? completedRuns.get(entry.runId)
      : undefined;
    if (!run) {
      looseEntries.push(entry);
      continue;
    }
    if (handledRuns.has(run.id)) {
      continue;
    }

    flushLooseEntries();
    handledRuns.add(run.id);
    const runEntries = entries.filter(
      (candidate) => candidate.runId === run.id,
    );
    blocks.push(...groupCompletedRun(run, runEntries));
  }
  flushLooseEntries();

  return blocks;
}

export type TimelineBlock =
  | TimelineContentBlock
  | {
      kind: "run-activity";
      id: string;
      runId: string;
      durationMs?: number;
      blocks: TimelineContentBlock[];
    };

function groupContentEntries(
  entries: TimelineEntry[],
  settleLast: boolean,
): TimelineContentBlock[] {
  const blocks: TimelineContentBlock[] = [];
  const segments: Array<{
    id: string;
    category: ToolGroupCategory;
    tools: ToolCall[];
    toolIds: Set<string>;
    active: boolean;
  }> = [];
  const segmentByToolId = new Map<
    string,
    (typeof segments)[number]
  >();
  let activeSegmentKey = "";
  for (const entry of entries) {
    if (entry.kind !== "tool") {
      continue;
    }
    const category = classifyToolCategory(entry.tool);
    const segmentKey = isUngroupableTool(entry.tool)
      ? `single:${entry.tool.id}`
      : `${entry.runId ?? ""}:${category}`;
    if (segmentKey !== activeSegmentKey) {
      segments.push({
        id: `${segmentKey}:${segments.length}`,
        category,
        tools: [],
        toolIds: new Set(),
        active: false,
      });
      activeSegmentKey = segmentKey;
    }
    const segment = segments.at(-1)!;
    segment.tools.push(entry.tool);
    segment.toolIds.add(entry.tool.id);
    segment.active ||= isActiveTool(entry.tool);
    segmentByToolId.set(entry.tool.id, segment);
  }
  const lastSegmentIndex = segments.length - 1;
  const groupedSegments = new Set<string>();
  for (const [index, segment] of segments.entries()) {
    if (
      !segment.active &&
      segment.tools.length >= 2 &&
      (index < lastSegmentIndex || settleLast)
    ) {
      groupedSegments.add(segment.id);
    }
  }
  const emittedGroups = new Set<string>();
  let thinking:
    | Extract<TimelineContentBlock, { kind: "run-thinking" }>
    | undefined;
  let durationKnown = true;

  for (const entry of entries) {
    if (entry.kind === "tool") {
      const segment = segmentByToolId.get(entry.tool.id);
      if (segment && groupedSegments.has(segment.id)) {
        if (!emittedGroups.has(segment.id)) {
          emittedGroups.add(segment.id);
          blocks.push({
            kind: "tool-group",
            category: segment.category,
            tools: segment.tools,
            summary: summarizeToolGroup(
              segment.category,
              segment.tools,
            ),
            failedCount: segment.tools.filter(
              (tool) => tool.status === "Failed",
            ).length,
            cancelledCount: segment.tools.filter(
              (tool) => tool.status === "Cancelled",
            ).length,
            ...(entry.runId ? { runId: entry.runId } : {}),
          });
        }
        continue;
      }
      appendToolCards(blocks, entry.tool, entry.runId);
      continue;
    }

    if (entry.kind === "approval") {
      continue;
    }

    if (entry.kind === "compaction") {
      blocks.push({
        kind: "compaction",
        compaction: entry.compaction,
      });
      continue;
    }

    if (entry.message.role === "user") {
      thinking = undefined;
      durationKnown = true;
      blocks.push({ kind: "entry", entry });
      continue;
    }

    const message = entry.message;
    if (message.thinking) {
      if (!thinking) {
        thinking = {
          kind: "run-thinking",
          id: `thinking-${message.id}`,
          ...(entry.runId ? { runId: entry.runId } : {}),
          thinking: "",
          active: false,
        };
        durationKnown = true;
        blocks.push(thinking);
      }
      thinking.thinking = thinking.thinking
        ? `${thinking.thinking}\n\n${message.thinking}`
        : message.thinking;
      thinking.active = isThinkingActive(message);
      if (message.thinkingDurationMs === undefined) {
        durationKnown = false;
        thinking.durationMs = undefined;
      } else if (durationKnown) {
        thinking.durationMs =
          (thinking.durationMs ?? 0) + message.thinkingDurationMs;
      }
    } else if (thinking) {
      thinking.active = false;
    }

    if (!message.content.trim()) {
      continue;
    }
    blocks.push({ kind: "entry", entry });
  }

  return blocks;
}

function appendToolCards(
  blocks: TimelineContentBlock[],
  tool: ToolCall,
  runId: string | undefined,
): void {
  const previous = blocks.at(-1);
  if (
    previous?.kind === "tools" &&
    previous.runId === runId
  ) {
    previous.tools.push(tool);
  } else {
    blocks.push({
      kind: "tools",
      tools: [tool],
      ...(runId ? { runId } : {}),
    });
  }
}

function isActiveTool(tool: ToolCall): boolean {
  return tool.status === "Running" || tool.status === "Waiting";
}

function isUngroupableTool(tool: ToolCall): boolean {
  return toolName(tool) === "ask_user";
}

export function classifyToolCategory(
  tool: ToolCall,
): ToolGroupCategory {
  const name = toolName(tool);
  if (
    [
      "read_file",
      "list_files",
      "glob",
      "grep",
      "search_codebase",
      "extract_document_text",
    ].includes(name)
  ) {
    return "explore";
  }
  if (["write_file", "edit_file"].includes(name)) {
    return "edit";
  }
  if (name === "run_command") {
    return "run";
  }
  if (name === "browser_bridge") {
    return "browser";
  }
  if (name === "web_search" || name === "web_fetch") {
    return "web";
  }
  if (name === "spawn_subagent") {
    return "collab";
  }
  if (
    [
      "load_skill",
      "create_plan",
      "update_plan",
      "todo_write",
    ].includes(name)
  ) {
    return "flow";
  }
  if (
    [
      "memory_list",
      "memory_write",
      "memory_delete",
    ].includes(name)
  ) {
    return "memory";
  }
  if (name.startsWith("mcp_")) {
    return "mcp";
  }
  return "other";
}

function summarizeToolGroup(
  category: ToolGroupCategory,
  tools: ToolCall[],
): string {
  if (category === "explore") {
    const filePaths = new Set(
      tools
        .filter((tool) =>
          ["read_file", "extract_document_text"].includes(
            toolName(tool),
          ),
        )
        .map((tool) => tool.target.trim())
        .filter(Boolean),
    );
    const searchCount = tools.filter((tool) =>
      [
        "list_files",
        "glob",
        "grep",
        "search_codebase",
      ].includes(toolName(tool)),
    ).length;
    const parts: string[] = [];
    if (filePaths.size > 0) {
      parts.push(`读取 ${filePaths.size} 个文件`);
    }
    if (searchCount > 0) {
      parts.push(`检索 ${searchCount} 次`);
    }
    return parts.join(" · ") || `其他探索 ${tools.length} 次`;
  }
  if (category === "edit") {
    const files = new Set(
      tools.map((tool) => tool.target.trim()).filter(Boolean),
    );
    let added = 0;
    let removed = 0;
    for (const tool of tools) {
      if (tool.detail?.kind === "diff") {
        added += tool.detail.added;
        removed += tool.detail.removed;
      }
    }
    const diff =
      added > 0 || removed > 0 ? ` · +${added} −${removed}` : "";
    return `修改 ${files.size} 个文件${diff}`;
  }
  if (category === "run") {
    return `执行 ${tools.length} 条命令`;
  }
  if (category === "browser") {
    return `浏览器操作 ${tools.length} 次`;
  }
  if (category === "web") {
    const searchCount = tools.filter(
      (tool) => toolName(tool) === "web_search",
    ).length;
    const fetchCount = tools.filter(
      (tool) => toolName(tool) === "web_fetch",
    ).length;
    return [
      searchCount > 0 ? `搜索 ${searchCount} 次` : "",
      fetchCount > 0 ? `抓取 ${fetchCount} 页` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (category === "collab") {
    return `子代理 ${tools.length} 个`;
  }
  if (category === "flow") {
    return `流程操作 ${tools.length} 次`;
  }
  if (category === "memory") {
    const readCount = tools.filter(
      (tool) => toolName(tool) === "memory_list",
    ).length;
    const writeCount = tools.filter((tool) =>
      ["memory_write", "memory_delete"].includes(toolName(tool)),
    ).length;
    return [
      readCount > 0 ? `查看 ${readCount} 次` : "",
      writeCount > 0 ? `更新 ${writeCount} 次` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (category === "mcp") {
    return `MCP 操作 ${tools.length} 次`;
  }
  return `其他操作 ${tools.length} 次`;
}

function toolName(tool: ToolCall): string {
  if (tool.name) {
    return tool.name;
  }
  const marker = tool.details.find((line) =>
    line.startsWith("Tool: "),
  );
  return marker?.slice("Tool: ".length).trim() ?? "";
}

function groupCompletedRun(
  run: TimelineRunView,
  entries: TimelineEntry[],
): TimelineBlock[] {
  const userIndex = entries.findIndex(
    (entry) =>
      entry.kind === "message" && entry.message.role === "user",
  );
  const finalIndex = findFinalAnswerIndex(entries);
  if (userIndex < 0 || finalIndex <= userIndex) {
    return groupContentEntries(entries, true);
  }

  const finalEntry = entries[finalIndex];
  if (
    finalEntry?.kind !== "message" ||
    finalEntry.message.role !== "assistant"
  ) {
    return groupContentEntries(entries, true);
  }

  const activityEntries = [
    ...entries.slice(userIndex + 1, finalIndex),
    ...(finalEntry.message.thinking
      ? [
          {
            ...finalEntry,
            message: {
              ...finalEntry.message,
              content: "",
            },
          },
        ]
      : []),
  ];
  const activityBlocks = groupContentEntries(activityEntries, true);
  const answer: Extract<TimelineEntry, { kind: "message" }> = {
    ...finalEntry,
    message: {
      ...finalEntry.message,
      thinking: undefined,
      thinkingDone: undefined,
      thinkingStartedAt: undefined,
      thinkingDurationMs: undefined,
    },
  };
  const userBlock: Extract<TimelineContentBlock, { kind: "entry" }> = {
    kind: "entry",
    entry: entries[userIndex] as Extract<
      TimelineEntry,
      { kind: "message" }
    >,
  };

  if (activityBlocks.length === 0) {
    return [
      userBlock,
      { kind: "entry", entry: answer },
    ];
  }

  const startedAt = run.startedAt;
  const completedAt = run.completedAt;
  const durationMs =
    startedAt !== undefined &&
    completedAt !== undefined &&
    completedAt >= startedAt
      ? completedAt - startedAt
      : undefined;

  return [
    userBlock,
    {
      kind: "run-activity",
      id: `run-activity-${run.id}`,
      runId: run.id,
      ...(durationMs !== undefined ? { durationMs } : {}),
      blocks: activityBlocks,
    },
    { kind: "entry", entry: answer },
    ...(run.changes?.length
      ? [
          {
            kind: "changes" as const,
            runId: run.id,
            changes: run.changes,
            reverted: run.changesReverted ?? false,
          },
        ]
      : []),
  ];
}

function findFinalAnswerIndex(entries: TimelineEntry[]): number {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index];
    if (entry?.kind === "tool") {
      return -1;
    }
    if (
      entry?.kind === "message" &&
      entry.message.role === "assistant" &&
      entry.message.content.trim()
    ) {
      return index;
    }
  }
  return -1;
}

export function isThinkingActive(
  message: TimelineMessage,
): boolean {
  return message.thinkingDone === undefined
    ? message.streaming
    : !message.thinkingDone;
}

export function formatThinkingDuration(
  durationMs: number,
): string {
  const seconds = Math.max(1, Math.round(durationMs / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`;
}

export function formatRunDuration(durationMs: number): string {
  const totalSeconds = Math.max(1, Math.round(durationMs / 1000));
  if (totalSeconds < 60) {
    return `${totalSeconds}秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0
    ? `${minutes}分钟 ${seconds}秒`
    : `${minutes}分钟`;
}

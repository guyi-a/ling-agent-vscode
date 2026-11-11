import type {
  CompactionItem,
  TimelineEntry,
  TimelineMessage,
  ToolCall,
} from "../types";

export type TimelineBlock =
  | {
      kind: "entry";
      entry: Extract<TimelineEntry, { kind: "message" }>;
    }
  | { kind: "tools"; tools: ToolCall[] }
  | { kind: "compaction"; compaction: CompactionItem }
  | {
      kind: "run-thinking";
      id: string;
      thinking: string;
      active: boolean;
      durationMs?: number;
    };

export function groupTimelineEntries(
  entries: TimelineEntry[],
): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  let thinking:
    | Extract<TimelineBlock, { kind: "run-thinking" }>
    | undefined;
  let durationKnown = true;

  for (const entry of entries) {
    if (entry.kind === "tool") {
      const previous = blocks.at(-1);
      if (previous?.kind === "tools") {
        previous.tools.push(entry.tool);
      } else {
        blocks.push({ kind: "tools", tools: [entry.tool] });
      }
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

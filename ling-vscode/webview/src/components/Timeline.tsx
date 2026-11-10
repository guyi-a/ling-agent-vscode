import { useLayoutEffect, useRef } from "react";
import type {
  CompactionItem,
  TimelineEntry,
  TimelineState,
  ToolCall,
  UserSelectionSummary,
} from "../types";
import { CompactionMarker } from "./CompactionMarker";
import { CheckIcon } from "./icons";
import { MarkdownMessage } from "./MarkdownMessage";
import { MessageAttachments } from "./MessageAttachments";
import { MessageSelections } from "./MessageSelections";
import { ToolCallCard } from "./ToolCallCard";

interface TimelineProps {
  state: TimelineState;
  onOpenAttachment(relativePath: string): void;
  onOpenSelection(selection: UserSelectionSummary): void;
}

type TimelineBlock =
  | {
      kind: "entry";
      entry: Extract<TimelineEntry, { kind: "message" }>;
    }
  | {
      kind: "tools";
      tools: ToolCall[];
    }
  | {
      kind: "approvals";
      approvals: Extract<TimelineEntry, { kind: "approval" }>["approval"][];
    }
  | {
      kind: "compaction";
      compaction: CompactionItem;
    };

export function Timeline({
  state,
  onOpenAttachment,
  onOpenSelection,
}: TimelineProps) {
  const blocks = groupTimelineEntries(state.entries);
  const timelineRef = useRef<HTMLElement>(null);
  const stickToBottom = useRef(true);
  const lastUserMessageId = useRef<string | undefined>(undefined);

  useLayoutEffect(() => {
    const timeline = timelineRef.current;
    if (!timeline) {
      return;
    }

    const lastUser = [...state.entries].reverse().find(
      (entry) =>
        entry.kind === "message" && entry.message.role === "user",
    );
    const lastUserId =
      lastUser?.kind === "message" ? lastUser.message.id : undefined;
    const hasNewUserMessage =
      lastUserId !== undefined && lastUserId !== lastUserMessageId.current;
    lastUserMessageId.current = lastUserId;

    if (hasNewUserMessage || stickToBottom.current) {
      timeline.scrollTop = timeline.scrollHeight;
      stickToBottom.current = true;
    }
  }, [state.entries, state.phase]);

  function handleScroll() {
    const timeline = timelineRef.current;
    if (!timeline) {
      return;
    }
    stickToBottom.current =
      timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 72;
  }

  return (
    <main
      ref={timelineRef}
      className="timeline"
      aria-label="Conversation"
      aria-live="polite"
      aria-relevant="additions text"
      onScroll={handleScroll}
    >
      {blocks.map((block) => {
        if (block.kind === "tools") {
          return (
            <div
              className="tool-stack"
              aria-label="Tool activity"
              key={getBlockKey(block)}
            >
              {block.tools.map((call) => (
                <ToolCallCard key={call.id} call={call} />
              ))}
            </div>
          );
        }

        if (block.kind === "approvals") {
          return (
            <div
              className="approval-history"
              aria-label="Approval history"
              key={getBlockKey(block)}
            >
              {block.approvals.map((approval) => (
                <div className="approval-history-row" key={approval.id}>
                  <span className="history-icon">
                    <CheckIcon />
                  </span>
                  <span>
                    {approval.approved ? "Approved once" : "Denied"} ·{" "}
                    {approval.target}
                  </span>
                </div>
              ))}
            </div>
          );
        }

        if (block.kind === "compaction") {
          return (
            <CompactionMarker
              key={getBlockKey(block)}
              item={block.compaction}
            />
          );
        }

        const { entry } = block;
        return (
          <section
            className={`message message--${entry.message.role}`}
            key={entry.message.id}
            data-selection-label={
              entry.message.role === "assistant" ? "Assistant" : "You"
            }
          >
            {entry.message.role === "assistant" ? (
              <MarkdownMessage
                content={entry.message.content}
                streaming={entry.message.streaming}
              />
            ) : (
              <>
                <span className="message-author">You</span>
                {entry.message.attachments?.length ? (
                  <MessageAttachments
                    attachments={entry.message.attachments}
                    onOpen={onOpenAttachment}
                  />
                ) : null}
                {entry.message.selections?.length ? (
                  <MessageSelections
                    selections={entry.message.selections}
                    onOpen={onOpenSelection}
                  />
                ) : null}
                {entry.message.content ? (
                  <p>{entry.message.content}</p>
                ) : null}
              </>
            )}
          </section>
        );
      })}

      {state.phase === "cancelled" ? (
        <section className="run-state" role="status">
          <span>Cancelled</span>
          <p>The run was stopped before pending tools completed.</p>
        </section>
      ) : null}

      {state.phase === "failed" && state.error ? (
        <section className="run-state run-state--error" role="alert">
          <span>Run failed</span>
          <p>{state.error}</p>
        </section>
      ) : null}
    </main>
  );
}

function groupTimelineEntries(entries: TimelineEntry[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];

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
      const previous = blocks.at(-1);
      if (previous?.kind === "approvals") {
        previous.approvals.push(entry.approval);
      } else {
        blocks.push({ kind: "approvals", approvals: [entry.approval] });
      }
      continue;
    }

    if (entry.kind === "compaction") {
      blocks.push({ kind: "compaction", compaction: entry.compaction });
      continue;
    }

    if (entry.message.role === "assistant" && !entry.message.content.trim()) {
      continue;
    }

    blocks.push({ kind: "entry", entry });
  }

  return blocks;
}

function getBlockKey(block: TimelineBlock): string {
  if (block.kind === "tools") {
    return `tools-${block.tools[0]?.id ?? "empty"}`;
  }

  if (block.kind === "approvals") {
    return `approvals-${block.approvals[0]?.id ?? "empty"}`;
  }

  if (block.kind === "compaction") {
    return block.compaction.id;
  }

  return block.entry.message.id;
}

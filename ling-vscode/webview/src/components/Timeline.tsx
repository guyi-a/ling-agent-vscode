import { useLayoutEffect, useRef } from "react";
import type {
  CompactionItem,
  TimelineEntry,
  TimelineState,
  ToolCall,
  UserSelectionSummary,
} from "../types";
import { CompactionMarker } from "./CompactionMarker";
import { MarkdownMessage } from "./MarkdownMessage";
import { MessageActions } from "./MessageActions";
import { MessageAttachments } from "./MessageAttachments";
import { MessageSelections } from "./MessageSelections";
import { ToolCallCard } from "./ToolCallCard";

interface TimelineProps {
  state: TimelineState;
  onOpenAttachment(relativePath: string): void;
  onOpenSelection(selection: UserSelectionSummary): void;
  onCopyMessage(text: string): void;
  onDeleteMessage(messageId: string): void;
  onRegenerateMessage(messageId: string): void;
  actionsDisabled: boolean;
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
      kind: "compaction";
      compaction: CompactionItem;
    };

export function Timeline({
  state,
  onOpenAttachment,
  onOpenSelection,
  onCopyMessage,
  onDeleteMessage,
  onRegenerateMessage,
  actionsDisabled,
}: TimelineProps) {
  const blocks = groupTimelineEntries(state.entries);
  const lastAssistantId = [...state.entries]
    .reverse()
    .find(
      (entry) =>
        entry.kind === "message" &&
        entry.message.role === "assistant",
    );
  const lastAssistantMessageId =
    lastAssistantId?.kind === "message"
      ? lastAssistantId.message.id
      : undefined;
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

        if (block.kind === "compaction") {
          return (
            <CompactionMarker
              key={getBlockKey(block)}
              item={block.compaction}
            />
          );
        }

        const { entry } = block;
        const thinkingActive =
          entry.message.thinkingDone === undefined
            ? entry.message.streaming
            : !entry.message.thinkingDone;
        const thinkingClass = [
          "thinking",
          thinkingActive ? "thinking--streaming" : "",
          entry.message.content ? "" : "thinking--only",
        ]
          .filter(Boolean)
          .join(" ");
        const thinkingLabel = thinkingActive
          ? "Thinking…"
          : entry.message.thinkingDurationMs !== undefined
            ? `Thought for ${formatThinkingDuration(
                entry.message.thinkingDurationMs,
              )}`
            : "Thoughts";
        return (
          <section
            className={`message message--${entry.message.role}`}
            key={entry.message.id}
            data-selection-label={
              entry.message.role === "assistant" ? "Assistant" : "You"
            }
          >
            {entry.message.role === "assistant" ? (
              <>
                {entry.message.thinking ? (
                  <details
                    className={thinkingClass}
                    aria-live="off"
                  >
                    <summary>
                      <span>{thinkingLabel}</span>
                      <span
                        className="thinking__chevron"
                        aria-hidden="true"
                      >
                        ›
                      </span>
                    </summary>
                    <pre className="thinking__text">
                      {entry.message.thinking}
                    </pre>
                  </details>
                ) : null}
                <MarkdownMessage
                  content={entry.message.content}
                  streaming={entry.message.streaming}
                />
                {!entry.message.streaming &&
                entry.message.content ? (
                  <MessageActions
                    text={entry.message.content}
                    disabled={actionsDisabled}
                    onCopy={onCopyMessage}
                    onDelete={() =>
                      onDeleteMessage(entry.message.id)
                    }
                    onRegenerate={
                      entry.message.id === lastAssistantMessageId
                        ? () =>
                            onRegenerateMessage(entry.message.id)
                        : undefined
                    }
                  />
                ) : null}
              </>
            ) : (
              <>
                <div className="message-bubble">
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
                </div>
                <MessageActions
                  text={entry.message.content}
                  disabled={actionsDisabled}
                  onCopy={onCopyMessage}
                  onDelete={() =>
                    onDeleteMessage(entry.message.id)
                  }
                />
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
      continue;
    }

    if (entry.kind === "compaction") {
      blocks.push({ kind: "compaction", compaction: entry.compaction });
      continue;
    }

    if (
      entry.message.role === "assistant" &&
      !entry.message.content.trim() &&
      !entry.message.thinking?.trim()
    ) {
      continue;
    }

    blocks.push({ kind: "entry", entry });
  }

  return blocks;
}

function formatThinkingDuration(durationMs: number): string {
  const seconds = Math.max(1, Math.round(durationMs / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function getBlockKey(block: TimelineBlock): string {
  if (block.kind === "tools") {
    return `tools-${block.tools[0]?.id ?? "empty"}`;
  }

  if (block.kind === "compaction") {
    return block.compaction.id;
  }

  return block.entry.message.id;
}

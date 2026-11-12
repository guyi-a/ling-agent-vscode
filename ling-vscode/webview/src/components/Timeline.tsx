import { useLayoutEffect, useRef } from "react";
import type { TimelineState, UserSelectionSummary } from "../types";
import {
  formatThinkingDuration,
  groupTimelineEntries,
} from "../state/timelineBlocks";
import type { TimelineBlock } from "../state/timelineBlocks";
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
  onOpenPlan(path: string): void;
  onBuildPlan(path: string): void;
  onCopyMessage(text: string): void;
  onDeleteMessage(messageId: string): void;
  onRegenerateMessage(messageId: string): void;
  actionsDisabled: boolean;
}

export function Timeline({
  state,
  onOpenAttachment,
  onOpenSelection,
  onOpenPlan,
  onBuildPlan,
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
                <ToolCallCard
                  key={call.id}
                  call={call}
                  subagentTrace={state.subagentTraces[call.id]}
                  actionsDisabled={actionsDisabled}
                  onOpenPlan={onOpenPlan}
                  onBuildPlan={onBuildPlan}
                />
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

        if (block.kind === "run-thinking") {
          const label = block.active
            ? "Thinking…"
            : block.durationMs !== undefined
              ? `Thought for ${formatThinkingDuration(
                  block.durationMs,
                )}`
              : "Thoughts";
          return (
            <details
              className={
                block.active
                  ? "thinking thinking--streaming"
                  : "thinking"
              }
              key={block.id}
              aria-live="off"
            >
              <summary>
                <span>{label}</span>
                <span
                  className="thinking__chevron"
                  aria-hidden="true"
                >
                  ›
                </span>
              </summary>
              <pre className="thinking__text">{block.thinking}</pre>
            </details>
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
              <>
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

function getBlockKey(block: TimelineBlock): string {
  if (block.kind === "tools") {
    return `tools-${block.tools[0]?.id ?? "empty"}`;
  }

  if (block.kind === "compaction") {
    return block.compaction.id;
  }

  if (block.kind === "run-thinking") {
    return block.id;
  }

  return block.entry.message.id;
}

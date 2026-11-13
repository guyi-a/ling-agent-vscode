import { useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type {
  TimelineState,
  ToolAction,
  UserSelectionSummary,
} from "../types";
import {
  formatRunDuration,
  formatThinkingDuration,
  groupTimelineEntries,
} from "../state/timelineBlocks";
import type {
  TimelineBlock,
  TimelineContentBlock,
  ToolGroupCategory,
} from "../state/timelineBlocks";
import { CompactionMarker } from "./CompactionMarker";
import { ChangeSummary } from "./ChangeSummary";
import { MarkdownMessage } from "./MarkdownMessage";
import { MessageActions } from "./MessageActions";
import { MessageAttachments } from "./MessageAttachments";
import { MessageSelections } from "./MessageSelections";
import { ToolCallCard } from "./ToolCallCard";
import { ToolIcon } from "./icons";

interface TimelineProps {
  state: TimelineState;
  onOpenAttachment(relativePath: string): void;
  onOpenSelection(selection: UserSelectionSummary): void;
  onOpenPlan(path: string): void;
  onBuildPlan(path: string): void;
  onCopyMessage(text: string): void;
  onDeleteMessage(messageId: string): void;
  onRegenerateMessage(messageId: string): void;
  onOpenChanges(runId: string): void;
  onRevertChanges(runId: string): void;
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
  onOpenChanges,
  onRevertChanges,
  actionsDisabled,
}: TimelineProps) {
  const blocks = groupTimelineEntries(state.entries, state.runs);
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
  const runTrackingSessionId = useRef<string | undefined>(undefined);
  const observedRunningRuns = useRef(new Set<string>());
  const completedLiveRuns = useRef(new Set<string>());

  if (runTrackingSessionId.current !== state.sessionId) {
    runTrackingSessionId.current = state.sessionId;
    observedRunningRuns.current.clear();
    completedLiveRuns.current.clear();
  }
  for (const run of state.runs ?? []) {
    if (run.status === "running") {
      observedRunningRuns.current.add(run.id);
    } else if (
      run.status === "completed" &&
      observedRunningRuns.current.has(run.id)
    ) {
      completedLiveRuns.current.add(run.id);
    }
  }

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
  }, [state.entries, state.phase, state.runs]);

  function handleScroll() {
    const timeline = timelineRef.current;
    if (!timeline) {
      return;
    }
    stickToBottom.current =
      timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 72;
  }

  function renderContentBlock(block: TimelineContentBlock) {
    if (block.kind === "tool-group") {
      return (
        <details
          className={`tool-group tool-group--${block.category}`}
          key={getBlockKey(block)}
        >
          <summary className="tool-group__summary">
            <span className="tool-group__icon">
              <ToolIcon
                action={categoryAction(block.category)}
              />
            </span>
            <span className="tool-group__label">
              {categoryLabel(block.category)}
            </span>
            <span className="tool-group__summary-text">
              {block.summary}
            </span>
            {block.failedCount > 0 ? (
              <span className="tool-group__failed">
                {block.failedCount} 失败
              </span>
            ) : null}
            {block.cancelledCount > 0 ? (
              <span className="tool-group__cancelled">
                {block.cancelledCount} 取消
              </span>
            ) : null}
            <span
              className="tool-group__chevron"
              aria-hidden="true"
            >
              ›
            </span>
          </summary>
          <div className="tool-stack tool-group__body">
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
        </details>
      );
    }

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

    if (block.kind === "changes") {
      return (
        <ChangeSummary
          key={getBlockKey(block)}
          changes={block.changes}
          reverted={block.reverted}
          viewDisabled={
            state.phase === "running" ||
            state.phase === "waiting_approval"
          }
          revertDisabled={
            actionsDisabled || state.readOnly || block.reverted
          }
          onView={() => onOpenChanges(block.runId)}
          onRevert={() => onRevertChanges(block.runId)}
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
                text={block.copyText ?? entry.message.content}
                disabled={actionsDisabled}
                showCopy={Boolean(block.copyText)}
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
        if (block.kind === "run-activity") {
          const summary =
            block.durationMs !== undefined
              ? `用时 ${formatRunDuration(block.durationMs)}`
              : "已完成";
          return (
            <RunActivityDetails
              initialOpen={completedLiveRuns.current.has(block.runId)}
              key={getBlockKey(block)}
            >
              <summary className="run-activity__summary">
                <span className="run-activity__meta">
                  {summary}
                </span>
                <span
                  className="run-activity__chevron"
                  aria-hidden="true"
                >
                  ›
                </span>
              </summary>
              <div
                className="run-activity__body"
                aria-label="Run activity"
              >
                {block.blocks.map(renderContentBlock)}
              </div>
            </RunActivityDetails>
          );
        }
        return renderContentBlock(block);
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

function RunActivityDetails({
  initialOpen,
  children,
}: {
  initialOpen: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <details
      className="run-activity"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      {children}
    </details>
  );
}

function getBlockKey(block: TimelineBlock): string {
  if (block.kind === "tools") {
    return `tools-${block.tools[0]?.id ?? "empty"}`;
  }

  if (block.kind === "tool-group") {
    return `tool-group-${block.category}-${block.tools[0]?.id ?? "empty"}`;
  }

  if (block.kind === "compaction") {
    return block.compaction.id;
  }

  if (block.kind === "run-thinking") {
    return block.id;
  }

  if (block.kind === "changes") {
    return `changes-${block.runId}`;
  }

  if (block.kind === "run-activity") {
    return block.id;
  }

  return block.entry.message.id;
}

function categoryLabel(category: ToolGroupCategory): string {
  return {
    explore: "探索",
    edit: "修改",
    run: "执行",
    browser: "浏览器",
    web: "联网",
    collab: "协作",
    flow: "流程",
    memory: "记忆",
    mcp: "MCP",
    other: "其他",
  }[category];
}

function categoryAction(category: ToolGroupCategory): ToolAction {
  return CATEGORY_ACTIONS[category];
}

const CATEGORY_ACTIONS: Record<ToolGroupCategory, ToolAction> = {
  explore: "Read",
  edit: "Edit",
  run: "Run",
  browser: "Browser",
  web: "Web",
  collab: "Subagent",
  flow: "Skill",
  memory: "Memory",
  mcp: "MCP",
  other: "Read",
};

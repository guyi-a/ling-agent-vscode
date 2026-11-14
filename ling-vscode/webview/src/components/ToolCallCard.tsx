import { useEffect, useRef, useState } from "react";
import type {
  BackgroundTaskView,
  SubagentTraceEntry,
  ToolAction,
  ToolCall,
  ToolDetail,
  ToolStatus,
} from "../types";
import { ChevronIcon, ToolIcon } from "./icons";
import { MarkdownMessage } from "./MarkdownMessage";
import { ToolComputer } from "./ToolComputer";

interface ToolCallCardProps {
  call: ToolCall;
  subagentTrace?: SubagentTraceEntry[];
  backgroundTask?: BackgroundTaskView;
  actionsDisabled?: boolean;
  readOnly?: boolean;
  backgroundRequested?: boolean;
  onOpenPlan?(path: string): void;
  onBuildPlan?(path: string): void;
  onBackground?(callId: string): void;
}

export function ToolCallCard({
  call,
  subagentTrace,
  backgroundTask,
  actionsDisabled = false,
  readOnly = false,
  backgroundRequested = false,
  onOpenPlan,
  onBuildPlan,
  onBackground,
}: ToolCallCardProps) {
  const backgroundMode =
    call.detail?.kind === "io" &&
    call.detail.mode === "background";
  const [expanded, setExpanded] = useState(
    call.detail?.kind === "plan" ||
      call.detail?.kind === "subagent" ||
      backgroundMode,
  );
  const effectiveStatus = backgroundTask
    ? statusForBackgroundTask(backgroundTask.status, call.status)
    : call.status;
  const statusClass = effectiveStatus.toLowerCase();

  useEffect(() => {
    if (
      call.detail?.kind === "plan" ||
      call.detail?.kind === "subagent" ||
      backgroundMode
    ) {
      setExpanded(true);
    }
  }, [backgroundMode, call.detail?.kind]);
  const diff =
    call.detail?.kind === "diff" ? call.detail : undefined;
  const canBackground =
    onBackground !== undefined &&
    call.name === "run_command" &&
    call.status === "Running" &&
    !backgroundTask &&
    !readOnly;

  return (
    <div className="tool-card-stack">
      <article className={`tool-card tool-card--${statusClass}`}>
        <button
        className="tool-card-row"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="tool-icon">
          <ToolIcon action={call.action} />
        </span>
        <span className="tool-action">{call.action}</span>
        <span className="tool-target">{call.target}</span>
        <span className={`tool-status tool-status--${statusClass}`}>
          {diff ? (
            <span
              className="tool-diff-stat"
              aria-label={`${diff.added} added, ${diff.removed} removed`}
            >
              <span className="tool-diff-stat__add">
                +{diff.added}
              </span>
              <span className="tool-diff-stat__remove">
                −{diff.removed}
              </span>
            </span>
          ) : null}
          <span className="status-dot" />
          {effectiveStatus}
        </span>
        <ChevronIcon className="tool-chevron" />
      </button>

        {expanded ? (
        <div
          className={`tool-details${
            call.detail ? " tool-details--rich" : ""
          }`}
        >
          {call.detail ? (
            call.detail.kind === "diff" ? (
              <ToolDiff detail={call.detail} />
            ) : call.detail.kind === "io" ? (
              <ToolIO
                detail={call.detail}
                backgroundTask={backgroundTask}
              />
            ) : call.detail.kind === "plan" ? (
              <ToolPlan
                detail={call.detail}
                disabled={actionsDisabled}
                onOpen={onOpenPlan}
                onBuild={onBuildPlan}
              />
            ) : call.detail.kind === "todo" ? (
              <ToolTodo detail={call.detail} />
            ) : call.detail.kind === "subagent" ? (
              <ToolSubagent
                detail={call.detail}
                trace={subagentTrace}
                backgroundTask={backgroundTask}
                status={call.status}
              />
            ) : call.detail.kind === "memory" ? (
              <ToolMemory detail={call.detail} />
            ) : call.detail.kind === "skill" ? (
              <ToolSkill detail={call.detail} />
            ) : call.detail.kind === "computer" ? (
              <ToolComputer detail={call.detail} />
            ) : (
              <pre className="tool-text">{call.detail.text}</pre>
            )
          ) : (
            call.details.map((line) => (
              <p key={line}>{line}</p>
            ))
          )}
        </div>
      ) : null}
      </article>
      {canBackground || backgroundRequested ? (
        <button
          className="tool-background-action"
          type="button"
          disabled={backgroundRequested}
          onClick={() => onBackground?.(call.id)}
        >
          {backgroundRequested
            ? "Moving to background…"
            : "Run in background"}
        </button>
      ) : null}
    </div>
  );
}

function ToolSkill({
  detail,
}: {
  detail: Extract<ToolDetail, { kind: "skill" }>;
}) {
  if (!detail.ok) {
    return (
      <div className="tool-memory-error" role="alert">
        {detail.error ?? "Unable to load skill."}
      </div>
    );
  }
  return (
    <div className="tool-skill__body">
      <MarkdownMessage content={detail.body} streaming={false} />
    </div>
  );
}

function ToolMemory({
  detail,
}: {
  detail: Extract<ToolDetail, { kind: "memory" }>;
}) {
  if (!detail.ok && detail.error) {
    return (
      <div className="tool-memory-error" role="alert">
        {detail.error}
      </div>
    );
  }

  if (detail.operation === "list") {
    const memories = detail.memories ?? [];
    return (
      <div className="tool-memory-list">
        <div className="tool-memory-list__title">
          {memories.length}{" "}
          {memories.length === 1 ? "memory" : "memories"}
        </div>
        {memories.length === 0 ? (
          <p className="tool-memory-empty">No memories found.</p>
        ) : (
          <ul className="tool-memory-items">
            {memories.map((memory) => (
              <li
                className="tool-memory-item"
                key={`${memory.scope}:${memory.name}`}
              >
                <div className="tool-memory-item__heading">
                  <span className="tool-memory-item__name">
                    {memory.name}
                  </span>
                  <span className="tool-memory-item__scope">
                    {memory.scope}
                  </span>
                  <span className="tool-memory-item__mode">
                    {memory.alwaysApply
                      ? "Always"
                      : memory.globs.length > 0
                        ? "Conditional"
                        : "Off"}
                  </span>
                </div>
                <p className="tool-memory-item__description">
                  {memory.description}
                </p>
                {memory.globs.length > 0 ? (
                  <code className="tool-memory-item__globs">
                    {memory.globs.join(", ")}
                  </code>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {detail.diagnostics?.length ? (
          <div className="tool-memory-diagnostics">
            {detail.diagnostics.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  const title =
    detail.operation === "delete"
      ? "Memory deleted"
      : "Memory saved";
  return (
    <div className="tool-memory-result">
      <div className="tool-memory-result__heading">
        <span>{title}</span>
        {detail.scope ? (
          <span className="tool-memory-item__scope">
            {detail.scope}
          </span>
        ) : null}
        {detail.name ? (
          <code className="tool-memory-result__name">
            {detail.name}
          </code>
        ) : null}
      </div>
      {detail.path ? (
        <code className="tool-memory-result__path">
          {detail.path}
        </code>
      ) : null}
    </div>
  );
}

function ToolDiff({
  detail,
}: {
  detail: Extract<ToolDetail, { kind: "diff" }>;
}) {
  return (
    <div className="tool-diff">
      <div className="tool-diff__path">{detail.path}</div>
      <div className="tool-diff__body">
        {detail.rows.map((row, index) => (
          <div
            className={`tool-diff__row tool-diff__row--${row.type}`}
            key={`${row.type}-${index}`}
          >
            <span className="tool-diff__line">
              {row.oldLine ?? ""}
            </span>
            <span className="tool-diff__line">
              {row.newLine ?? ""}
            </span>
            <span className="tool-diff__marker">
              {row.type === "add"
                ? "+"
                : row.type === "remove"
                  ? "-"
                  : " "}
            </span>
            <span className="tool-diff__text">{row.text}</span>
          </div>
        ))}
      </div>
      {detail.truncated ? (
        <div className="tool-diff__truncated">Diff truncated</div>
      ) : null}
    </div>
  );
}

function ToolPlan({
  detail,
  disabled = false,
  onOpen,
  onBuild,
}: {
  detail: Extract<ToolDetail, { kind: "plan" }>;
  disabled?: boolean;
  onOpen?: (path: string) => void;
  onBuild?: (path: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const long = detail.text.length > 1_200;

  return (
    <div className="tool-plan">
      <div className="tool-plan__header">
        <span className="tool-plan__path" title={detail.path}>
          {detail.path}
        </span>
        <div className="tool-plan__actions">
          {onOpen ? (
            <button
              className="tool-plan__button"
              type="button"
              title="Open the plan markdown source"
              onClick={() => onOpen(detail.path)}
            >
              Open source
            </button>
          ) : null}
          {onBuild ? (
            <button
              className="tool-plan__button tool-plan__button--build"
              type="button"
              disabled={disabled}
              onClick={() => onBuild(detail.path)}
            >
              Build
            </button>
          ) : null}
        </div>
      </div>
      <div
        className={`tool-plan__body${
          long && !showAll ? " tool-plan__body--clamped" : ""
        }`}
      >
        <MarkdownMessage content={detail.text} streaming={false} />
      </div>
      {long ? (
        <button
          className="tool-plan__more"
          type="button"
          onClick={() => setShowAll((value) => !value)}
        >
          {showAll ? "Show less" : "Show full plan"}
        </button>
      ) : null}
    </div>
  );
}

function ToolTodo({
  detail,
}: {
  detail: Extract<ToolDetail, { kind: "todo" }>;
}) {
  return (
    <div className="tool-todo">
      {detail.todos.map((todo) => (
        <div
          className={`tool-todo__item tool-todo__item--${todo.status}`}
          key={todo.id}
        >
          <span className="tool-todo__mark" aria-hidden="true">
            {todo.status === "completed"
              ? "✓"
              : todo.status === "in_progress"
                ? "•"
                : todo.status === "cancelled"
                  ? "×"
                  : "○"}
          </span>
          <span className="tool-todo__text">{todo.content}</span>
        </div>
      ))}
    </div>
  );
}

function ToolSubagent({
  detail,
  trace,
  backgroundTask,
  status,
}: {
  detail: Extract<ToolDetail, { kind: "subagent" }>;
  trace?: SubagentTraceEntry[];
  backgroundTask?: BackgroundTaskView;
  status: ToolStatus;
}) {
  const live = trace ?? [];
  const persisted = detail.trace ?? [];
  // A compacted/late live stream can be partial; persisted details are whole.
  const entries = persisted.length >= live.length ? persisted : live;
  const toolCount = entries.filter(
    (entry) => entry.kind === "tool_call",
  ).length;
  const background = detail.mode === "background";
  const taskStatus = backgroundTask?.status ?? detail.status;
  const summary = backgroundTask?.summary ?? detail.summary;
  const error = backgroundTask?.error ?? detail.error;
  const running = background
    ? taskStatus === "pending" || taskStatus === "running"
    : status === "Running" || status === "Waiting";
  const [showTrace, setShowTrace] = useState(running);
  const traceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Live while running; after completion the summary is the main view.
    setShowTrace(running);
  }, [running]);

  useEffect(() => {
    const element = traceRef.current;
    if (showTrace && running && element) {
      element.scrollTop = element.scrollHeight;
    }
  }, [entries.length, showTrace, running]);

  return (
    <div className="tool-subagent">
      <div className="tool-subagent__header">
        <span className="tool-subagent__description">
          {detail.description}
        </span>
        <span className="tool-subagent__badge">
          {detail.subagentType} · {detail.mode}
          {background && taskStatus ? ` · ${taskStatus}` : ""}
        </span>
      </div>

      {summary ? (
        <div className="tool-subagent__summary">
          <MarkdownMessage content={summary} streaming={false} />
        </div>
      ) : null}

      {error && !summary ? (
        <p className="tool-subagent__error">{error}</p>
      ) : null}

      {running && entries.length === 0 ? (
        <p className="tool-subagent__empty">
          {background ? "Running in background…" : "Working…"}
        </p>
      ) : null}

      {entries.length > 0 ? (
        <div className="tool-subagent__trace">
          <button
            className="tool-subagent__trace-toggle"
            type="button"
            onClick={() => setShowTrace((value) => !value)}
          >
            {showTrace
              ? `Hide trace · ${toolCount} tools`
              : `View trace · ${toolCount} tools`}
          </button>
          {showTrace ? (
            <div
              className="tool-subagent__trace-body"
              ref={traceRef}
            >
              <SubagentTraceEntries entries={entries} />
            </div>
          ) : null}
        </div>
      ) : null}

      {!running && !summary && !error && entries.length === 0 ? (
        <p className="tool-subagent__empty">No summary returned.</p>
      ) : null}
    </div>
  );
}

type SubagentToolCall = Extract<
  SubagentTraceEntry,
  { kind: "tool_call" }
>;
type SubagentToolResult = Extract<
  SubagentTraceEntry,
  { kind: "tool_result" }
>;
type SubagentDisplayItem =
  | {
      kind: "assistant";
      entry: Extract<SubagentTraceEntry, { kind: "assistant" }>;
    }
  | {
      kind: "tool";
      call: SubagentToolCall;
      result?: SubagentToolResult;
    }
  | {
      kind: "error";
      entry: Extract<SubagentTraceEntry, { kind: "error" }>;
    };

function groupSubagentTrace(
  entries: SubagentTraceEntry[],
): SubagentDisplayItem[] {
  const items: SubagentDisplayItem[] = [];
  const toolIndexes = new Map<string, number>();
  for (const entry of entries) {
    if (entry.kind === "tool_call") {
      toolIndexes.set(entry.id, items.length);
      items.push({ kind: "tool", call: entry });
      continue;
    }
    if (entry.kind === "tool_result") {
      const index = toolIndexes.get(entry.id);
      if (index === undefined) {
        items.push({
          kind: "tool",
          call: {
            kind: "tool_call",
            id: entry.id,
            name: entry.name,
            arguments: {},
          },
          result: entry,
        });
        continue;
      }
      const item = items[index];
      if (item.kind === "tool") {
        item.result = entry;
      }
      continue;
    }
    if (entry.kind === "assistant") {
      items.push({ kind: "assistant", entry });
      continue;
    }
    items.push({ kind: "error", entry });
  }
  return items;
}

function SubagentTraceEntries({
  entries,
}: {
  entries: SubagentTraceEntry[];
}) {
  const items = groupSubagentTrace(entries);
  return (
    <>
      {items.map((item, index) => {
        if (item.kind === "tool") {
          return (
            <SubagentToolCard
              call={item.call}
              result={item.result}
              key={`tool-${item.call.id}-${index}`}
            />
          );
        }
        if (item.kind === "assistant") {
          const entry = item.entry;
          return (
            <div
              className="tool-subagent__trace-entry"
              key={`assistant-${entry.id}-${index}`}
            >
              <span className="tool-subagent__trace-label">
                Assistant
              </span>
              {entry.thinking ? (
                <details className="tool-subagent__thinking">
                  <summary>Thinking</summary>
                  <pre>{entry.thinking}</pre>
                </details>
              ) : null}
              {entry.text ? (
                <MarkdownMessage
                  content={entry.text}
                  streaming={false}
                />
              ) : null}
            </div>
          );
        }
        return (
          <div
            className="tool-subagent__trace-entry tool-subagent__trace-entry--error"
            key={`error-${index}`}
          >
            <span className="tool-subagent__trace-label">Error</span>
            <p>{item.entry.message}</p>
          </div>
        );
      })}
    </>
  );
}

function SubagentToolCard({
  call,
  result,
}: {
  call: SubagentToolCall;
  result?: SubagentToolResult;
}) {
  const [expanded, setExpanded] = useState(false);
  const action = subagentToolAction(call.name);
  const status = result
    ? result.isError
      ? "Failed"
      : "Completed"
    : "Running";
  const statusClass = status.toLowerCase();
  const statusStyle = result ? statusClass : "waiting";

  return (
    <article className={`subagent-tool subagent-tool--${statusClass}`}>
      <button
        className="subagent-tool__row"
        type="button"
        aria-expanded={expanded}
        onClick={(event) => {
          event.stopPropagation();
          setExpanded((value) => !value);
        }}
      >
        <span className="subagent-tool__icon">
          <ToolIcon action={action} />
        </span>
        <span className="subagent-tool__action">{action}</span>
        <span className="subagent-tool__target">
          {subagentToolTarget(call)}
        </span>
        <span
          className={`subagent-tool__status subagent-tool__status--${statusStyle}`}
        >
          <span className="status-dot" />
          {status}
        </span>
        <ChevronIcon className="subagent-tool__chevron" />
      </button>
      {expanded ? (
        <div className="subagent-tool__details">
          <div className="tool-io">
            <div className="tool-io__section">
              <div className="tool-io__label">IN</div>
              <pre className="tool-io__text">
                {JSON.stringify(call.arguments, null, 2)}
              </pre>
            </div>
            <div className="tool-io__section">
              <div className="tool-io__label">OUT</div>
              <pre
                className={`tool-io__text${
                  result?.isError ? " tool-io__text--error" : ""
                }`}
              >
                {result
                  ? result.output || "(no output)"
                  : "Running…"}
              </pre>
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}

function subagentToolAction(name: string): ToolAction {
  if (
    name === "read_file" ||
    name === "list_files" ||
    name === "grep" ||
    name === "glob" ||
    name === "search_codebase"
  ) {
    return "Read";
  }
  return "Run";
}

function subagentToolTarget(call: SubagentToolCall): string {
  for (const key of ["path", "query", "pattern", "name", "command"]) {
    const value = call.arguments[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return call.name;
}

function statusForBackgroundTask(
  status: string,
  fallback: ToolStatus,
): ToolStatus {
  if (status === "pending" || status === "running") {
    return "Running";
  }
  if (status === "completed") {
    return "Completed";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "cancelled" || status === "interrupted") {
    return "Cancelled";
  }
  return fallback;
}

function ToolIO({
  detail,
  backgroundTask,
}: {
  detail: Extract<ToolDetail, { kind: "io" }>;
  backgroundTask?: BackgroundTaskView;
}) {
  const output = detail.output || detail.error || "(no output)";
  return (
    <div className="tool-io">
      {detail.input ? (
        <div className="tool-io__section">
          <div className="tool-io__label">IN</div>
          <pre className="tool-io__text">{detail.input}</pre>
        </div>
      ) : null}
      <div className="tool-io__section">
        <div className="tool-io__label">OUT</div>
        <pre
          className={`tool-io__text${
            detail.error ? " tool-io__text--error" : ""
          }`}
        >
          {output}
        </pre>
      </div>
      {detail.exitCode !== undefined ? (
        <div className="tool-io__exit">exit {detail.exitCode}</div>
      ) : null}
      {backgroundTask ? (
        <div
          className={`tool-io__background tool-io__background--${backgroundTask.status}`}
        >
          <span>Background · {backgroundTask.status}</span>
          {backgroundTask.exitCode !== undefined &&
          backgroundTask.exitCode !== null ? (
            <span>exit {backgroundTask.exitCode}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

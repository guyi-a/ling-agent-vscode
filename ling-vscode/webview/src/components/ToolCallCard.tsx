import { useState } from "react";
import type { ToolCall, ToolDetail } from "../types";
import { ChevronIcon, ToolIcon } from "./icons";

interface ToolCallCardProps {
  call: ToolCall;
}

export function ToolCallCard({ call }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const statusClass = call.status.toLowerCase();
  const diff =
    call.detail?.kind === "diff" ? call.detail : undefined;

  return (
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
          {call.status}
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
              <ToolIO detail={call.detail} />
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

function ToolIO({
  detail,
}: {
  detail: Extract<ToolDetail, { kind: "io" }>;
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
    </div>
  );
}

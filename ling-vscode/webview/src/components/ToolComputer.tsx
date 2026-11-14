import type { ToolDetail } from "../types";

type ComputerDetail = Extract<ToolDetail, { kind: "computer" }>;

export function ToolComputer({ detail }: { detail: ComputerDetail }) {
  const title =
    detail.operation === "list"
      ? "Desktop inventory"
      : detail.operation === "observe"
        ? "UI Automation snapshot"
        : detail.operation === "launch"
          ? "Application launch"
          : detail.operation === "screenshot"
            ? "Window screenshot"
            : detail.operation === "focus"
              ? "Window focus"
              : detail.operation === "stop"
                ? "Control session"
                : "Computer action";
  return (
    <div className="tool-computer">
      <div className="tool-computer__meta">
        <strong>{title}</strong>
        {detail.app ? <span>{detail.app}</span> : null}
        {detail.title ? (
          <span className="tool-computer__title">{detail.title}</span>
        ) : null}
        {detail.windowId ? <code>{detail.windowId}</code> : null}
        {typeof detail.pid === "number" && detail.pid > 0 ? (
          <span>pid {detail.pid}</span>
        ) : null}
        {detail.reused ? <span>reused window</span> : null}
        {detail.timedOut ? <span>window timeout</span> : null}
        {detail.foreground === true ? <span>foreground</span> : null}
        {detail.foreground === false ? (
          <span className="tool-computer__error">not foreground</span>
        ) : null}
        {detail.restored ? <span>restored</span> : null}
        {typeof detail.width === "number" &&
        typeof detail.height === "number" ? (
          <span>
            {detail.width}×{detail.height}
            {typeof detail.scale === "number" && detail.scale < 1
              ? ` · ${Math.round(detail.scale * 100)}%`
              : ""}
          </span>
        ) : null}
        {detail.captureId ? <code>{detail.captureId}</code> : null}
        {detail.snapshotId ? <code>{detail.snapshotId}</code> : null}
        {detail.observationId ? (
          <code>{detail.observationId}</code>
        ) : null}
        {typeof detail.windowCount === "number" ? (
          <span>{detail.windowCount} windows</span>
        ) : null}
        {typeof detail.displayCount === "number" ? (
          <span>{detail.displayCount} displays</span>
        ) : null}
        {typeof detail.nodeCount === "number" ? (
          <span>{detail.nodeCount} nodes</span>
        ) : null}
        {detail.complete === false || detail.truncated ? (
          <span>partial</span>
        ) : null}
        {detail.error ? (
          <span className="tool-computer__error">{detail.error}</span>
        ) : null}
      </div>
      {detail.preview ? (
        <pre className="tool-text">{detail.preview}</pre>
      ) : null}
    </div>
  );
}

import { useState } from "react";
import type { ToolCall } from "../types";
import { ChevronIcon, ToolIcon } from "./icons";

interface ToolCallCardProps {
  call: ToolCall;
}

export function ToolCallCard({ call }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const statusClass = call.status.toLowerCase();

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
          <span className="status-dot" />
          {call.status}
        </span>
        <ChevronIcon className="tool-chevron" />
      </button>

      {expanded ? (
        <div className="tool-details">
          {call.details.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : null}
    </article>
  );
}

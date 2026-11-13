import { useEffect, useRef, useState } from "react";
import type { ApprovalRequest } from "../types";
import { ChevronIcon, ToolIcon } from "./icons";

interface ApprovalTakeoverProps {
  request: ApprovalRequest;
  index: number;
  total: number;
  onResolve(
    approved: boolean,
    options?: { remember?: boolean; feedback?: string },
  ): void;
  onAllowAlways(): void;
  onCancelRun(): void;
}

export function ApprovalTakeover({
  request,
  index,
  total,
  onResolve,
  onAllowAlways,
  onCancelRun,
}: ApprovalTakeoverProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const feedbackRef = useRef<HTMLInputElement>(null);
  const allowAlways = request.allowAlways;

  useEffect(() => {
    setDetailsOpen(false);
    setFeedback("");
  }, [request.id]);

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
      if (event.key === "Escape") {
        event.preventDefault();
        onCancelRun();
        return;
      }
      if (typing) {
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      if (event.key === "1") {
        event.preventDefault();
        onResolve(true);
      } else if (event.key === "2" && allowAlways) {
        event.preventDefault();
        onAllowAlways();
      } else if (event.key === "3") {
        event.preventDefault();
        onResolve(false);
      } else if (event.key === "4") {
        event.preventDefault();
        feedbackRef.current?.focus();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [allowAlways, onAllowAlways, onCancelRun, onResolve]);

  const fileName = baseName(request.target);
  const title =
    request.tool === "write_file"
      ? `Allow write to ${request.target}?`
      : request.tool === "edit_file"
        ? `Allow editing ${request.target}?`
        : request.tool === "run_command"
          ? "Allow running this command?"
          : `Allow ${request.action} to ${request.target}?`;
  const reason =
    request.tool === "edit_file" || request.tool === "write_file"
      ? "Review the proposed change before applying it."
      : request.reason;

  return (
    <section className="approval-takeover" aria-label="Approval required">
      <div className="approval-heading">
        <div className="approval-title">
          <ToolIcon action={request.action} className="approval-icon" />
          <div>
            <p className="approval-kicker">Approval required</p>
            <h2 title={request.target}>{title}</h2>
          </div>
        </div>
        {total > 1 ? (
          <span className="approval-queue">
            {index + 1} / {total}
          </span>
        ) : null}
      </div>

      {request.tool === "run_command" ? (
        <pre className="approval-command">{request.target}</pre>
      ) : (
        <p className="approval-path" title={request.target}>
          {fileName}
        </p>
      )}

      {reason ? <p className="approval-reason">{reason}</p> : null}

      <button
        className="details-toggle"
        type="button"
        aria-expanded={detailsOpen}
        onClick={() => setDetailsOpen((current) => !current)}
      >
        <ChevronIcon className="details-chevron" />
        Details
      </button>

      {detailsOpen ? (
        <div className="approval-details">
          {request.details.map((line, lineIndex) => (
            <p key={`${lineIndex}-${line}`}>{line}</p>
          ))}
        </div>
      ) : null}

      <div className="approval-options">
        <button
          className="approval-option approval-option--primary"
          type="button"
          onClick={() => onResolve(true)}
        >
          <span className="approval-option-key">1</span>
          <span>Yes</span>
        </button>
        {allowAlways ? (
          <button
            className="approval-option"
            type="button"
            onClick={onAllowAlways}
          >
            <span className="approval-option-key">2</span>
            <span>{allowAlways.label}</span>
          </button>
        ) : null}
        <button
          className="approval-option"
          type="button"
          onClick={() => onResolve(false)}
        >
          <span className="approval-option-key">3</span>
          <span>No</span>
        </button>
        <div
          className="approval-option approval-option--feedback"
          onClick={() => feedbackRef.current?.focus()}
        >
          <span className="approval-option-key">4</span>
          <input
            ref={feedbackRef}
            value={feedback}
            placeholder="Tell Ling what to do instead"
            onChange={(event) => setFeedback(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && feedback.trim()) {
                event.preventDefault();
                onResolve(false, { feedback: feedback.trim() });
              }
            }}
          />
        </div>
      </div>

      <div className="approval-footer">
        <button
          className="approval-esc"
          type="button"
          onClick={onCancelRun}
        >
          Esc to cancel
        </button>
      </div>
    </section>
  );
}

function baseName(value: string): string {
  const parts = value.split(/[\\/]/);
  return parts.at(-1) || value;
}

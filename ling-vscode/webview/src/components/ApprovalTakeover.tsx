import { useEffect, useState } from "react";
import type { ApprovalRequest } from "../types";
import { ChevronIcon, ToolIcon } from "./icons";

interface ApprovalTakeoverProps {
  request: ApprovalRequest;
  index: number;
  total: number;
  onResolve(approved: boolean): void;
  onCancelRun(): void;
}

export function ApprovalTakeover({
  request,
  index,
  total,
  onResolve,
  onCancelRun,
}: ApprovalTakeoverProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    setDetailsOpen(false);
  }, [request.id]);

  const isEdit = request.action === "Edit";
  const fileName = baseName(request.target);
  const title = isEdit
    ? `Edit ${fileName}`
    : request.action === "Run"
      ? "Run command"
      : `${request.action} ${fileName}`;
  const reason =
    isEdit && /^(edit|write)\s/i.test(request.reason)
      ? "Review the proposed change before applying it."
      : request.reason;

  return (
    <section className="approval-takeover" aria-label="Approval required">
      <div className="approval-heading">
        <div className="approval-title">
          <ToolIcon action={request.action} className="approval-icon" />
          <div>
            <p className="approval-kicker">Approval required</p>
            <h2>{title}</h2>
          </div>
        </div>
        {total > 1 ? (
          <span className="approval-queue">
            {index + 1} / {total}
          </span>
        ) : null}
      </div>

      {isEdit ? (
        <p className="approval-path" title={request.target}>
          {request.target}
        </p>
      ) : (
        <pre className="approval-command">{request.target}</pre>
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

      <div className="approval-footer">
        <button
          className="cancel-run"
          type="button"
          onClick={onCancelRun}
        >
          Cancel run
        </button>
        <div className="approval-actions">
          <button
            className="button button--secondary"
            type="button"
            onClick={() => onResolve(false)}
          >
            Deny
          </button>
          <button
            className="button button--primary"
            type="button"
            onClick={() => onResolve(true)}
          >
            Allow once
          </button>
        </div>
      </div>
    </section>
  );
}

function baseName(value: string): string {
  const parts = value.split(/[\\/]/);
  return parts.at(-1) || value;
}

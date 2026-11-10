import { useEffect, useState } from "react";
import type { HistorySession } from "../../../shared/uiTypes";
import { TrashIcon } from "./icons";

interface HistoryPanelProps {
  sessions: HistorySession[];
  loading: boolean;
  deleting: boolean;
  switchingDisabled: boolean;
  query: string;
  error: string;
  onQueryChange(query: string): void;
  onSelect(sessionId: string): void;
  onDelete(sessionId: string): void;
  onClose(): void;
  onNewSession(): void;
}

export function HistoryPanel({
  sessions,
  loading,
  deleting,
  switchingDisabled,
  query,
  error,
  onQueryChange,
  onSelect,
  onDelete,
  onClose,
  onNewSession,
}: HistoryPanelProps) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  useEffect(() => {
    if (
      confirmingId &&
      !sessions.some((session) => session.id === confirmingId)
    ) {
      setConfirmingId(null);
    }
  }, [confirmingId, sessions]);

  useEffect(() => {
    if (!confirmingId) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setConfirmingId(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirmingId]);

  return (
    <section className="history-panel" aria-label="Conversation history">
      <header className="history-panel-header">
        <div>
          <h2>Conversation history</h2>
          <p>Open a recent session or start a new one.</p>
        </div>
        <button
          className="history-close"
          type="button"
          aria-label="Close history"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <div className="history-search-wrap">
        <input
          className="history-search"
          type="search"
          value={query}
          placeholder="Search conversations..."
          aria-label="Search conversations"
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </div>

      <div className="history-list">
        {switchingDisabled ? (
          <p className="history-busy-note">
            Stop the current run before switching conversations.
          </p>
        ) : null}
        {error ? (
          <p className="history-error" role="alert">
            {error}
          </p>
        ) : null}
        {loading ? (
          <div className="history-empty">
            <p>Loading conversations...</p>
          </div>
        ) : sessions.length === 0 ? (
          <div className="history-empty">
            <p>
              {query.trim()
                ? "No matching conversations."
                : "No conversations yet."}
            </p>
          </div>
        ) : (
          sessions.map((session) => {
            const confirming = confirmingId === session.id;
            return (
              <div
                className={`history-item${confirming ? " history-item--confirming" : ""}`}
                key={session.id}
              >
                <div className="history-item-row">
                  <button
                    className="history-item-open"
                    type="button"
                    disabled={switchingDisabled || deleting}
                    onClick={() => onSelect(session.id)}
                  >
                    <span className="history-item-title">
                      {session.title}
                    </span>
                    {session.snippet ? (
                      <span className="history-item-snippet">
                        {session.snippet}
                      </span>
                    ) : null}
                    <span className="history-item-meta">
                      {workspaceName(session.workspaceRoot)} ·{" "}
                      {formatDate(session.updatedAt)}
                    </span>
                  </button>
                  {confirming ? (
                    <div
                      className="history-item-confirm"
                      role="group"
                      aria-label={`Confirm deleting ${session.title}`}
                      title="Delete this conversation? This cannot be undone."
                    >
                      <div className="history-item-confirm-actions">
                        <button
                          className="history-confirm-cancel"
                          type="button"
                          autoFocus
                          onClick={() => setConfirmingId(null)}
                        >
                          Cancel
                        </button>
                        <button
                          className="history-confirm-delete"
                          type="button"
                          disabled={deleting}
                          onClick={() => {
                            setConfirmingId(null);
                            onDelete(session.id);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      className="history-item-delete"
                      type="button"
                      disabled={switchingDisabled || deleting}
                      aria-label={`Delete ${session.title}`}
                      title="Delete conversation"
                      onClick={() => setConfirmingId(session.id)}
                    >
                      <TrashIcon />
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      <footer className="history-panel-footer">
        <button
          className="button button--primary history-new-button"
          type="button"
          disabled={switchingDisabled}
          onClick={onNewSession}
        >
          New session
        </button>
      </footer>
    </section>
  );
}

function workspaceName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? path;
}

function formatDate(timestamp: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

import { LingMarkIcon } from "./icons";

export function EmptyState() {
  return (
    <section className="empty-state" aria-label="Start a conversation">
      <div className="empty-state-inner">
        <span className="empty-mark" aria-hidden="true">
          <LingMarkIcon />
        </span>
        <h2 className="empty-title">Ready when you are.</h2>
        <p className="empty-subtitle">
          Ask about the project, make a change, or run a command.
        </p>
      </div>
    </section>
  );
}

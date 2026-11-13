import type { BackgroundTaskView } from "../types";

interface TasksPanelProps {
  tasks: BackgroundTaskView[];
  readOnly?: boolean;
  onStop(taskId: string): void;
  onOpenLog(taskId: string): void;
  onClose(): void;
}

export function TasksPanel({
  tasks,
  readOnly = false,
  onStop,
  onOpenLog,
  onClose,
}: TasksPanelProps) {
  const ordered = [...tasks].sort(
    (left, right) => right.createdAt - left.createdAt,
  );

  return (
    <section className="history-panel tasks-panel" aria-label="Background tasks">
      <header className="history-panel-header">
        <div>
          <h2>Background tasks</h2>
          <p>Only the current session is shown.</p>
        </div>
        <button
          className="history-close"
          type="button"
          aria-label="Close tasks"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <div className="history-list">
        {ordered.length === 0 ? (
          <p className="history-empty">No background tasks in this session.</p>
        ) : (
          ordered.map((task) => {
            const active =
              task.status === "pending" || task.status === "running";
            return (
              <article className="task-row" key={task.id}>
                <div className="task-row__heading">
                  <span
                    className={`task-row__status task-row__status--${task.status}`}
                  >
                    {task.status}
                  </span>
                  <span className="task-row__kind">
                    {task.kind === "command" ? "Command" : "Subagent"}
                  </span>
                  <span
                    className="task-row__description"
                    title={task.description}
                  >
                    {task.description}
                  </span>
                  {task.exitCode !== undefined &&
                  task.exitCode !== null ? (
                    <span className="task-row__exit">
                      exit {task.exitCode}
                    </span>
                  ) : null}
                  {active && !readOnly ? (
                    <button
                      className="task-row__stop"
                      type="button"
                      onClick={() => onStop(task.id)}
                    >
                      Stop
                    </button>
                  ) : null}
                  {task.outputPath ? (
                    <button
                      className="task-row__log"
                      type="button"
                      onClick={() => onOpenLog(task.id)}
                    >
                      View log
                    </button>
                  ) : null}
                </div>
                <code className="task-row__id">{task.id}</code>
                {task.summary ? (
                  <p className="task-row__summary">{task.summary}</p>
                ) : task.error ? (
                  <p className="task-row__error">{task.error}</p>
                ) : null}
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}

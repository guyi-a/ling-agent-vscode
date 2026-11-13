import {
  HistoryIcon,
  PlusIcon,
  SettingsIcon,
  TasksIcon,
} from "./icons";

interface HeaderProps {
  title: string;
  isNew: boolean;
  historyAvailable: boolean;
  tasksAvailable: boolean;
  taskCount: number;
  settingsDisabled: boolean;
  onHistory(): void;
  onTasks(): void;
  onSettings(): void;
  onReset(): void;
}

export function Header({
  title,
  isNew,
  historyAvailable,
  tasksAvailable,
  taskCount,
  settingsDisabled,
  onHistory,
  onTasks,
  onSettings,
  onReset,
}: HeaderProps) {
  return (
    <header className="chat-header">
      <div className="workspace-title" title={title}>
        <span className="workspace-indicator" aria-hidden="true" />
        <span>{title}</span>
      </div>

      <div className="header-actions">
        {tasksAvailable ? (
          <button
            className="icon-button icon-button--badged"
            type="button"
            aria-label="Background tasks"
            title="Background tasks"
            onClick={onTasks}
          >
            <TasksIcon />
            {taskCount > 0 ? (
              <span className="icon-button-badge">{taskCount}</span>
            ) : null}
          </button>
        ) : null}
        {historyAvailable ? (
          <button
            className="icon-button"
            type="button"
            aria-label="Conversation history"
            title="Conversation history"
            onClick={onHistory}
          >
            <HistoryIcon />
          </button>
        ) : null}
        <button
          className="icon-button"
          type="button"
          aria-label="Settings"
          title="Settings"
          disabled={settingsDisabled}
          onClick={onSettings}
        >
          <SettingsIcon />
        </button>
        {!isNew ? (
          <button
            className="icon-button"
            type="button"
            aria-label="New session"
            title="New session"
            onClick={onReset}
          >
            <PlusIcon />
          </button>
        ) : null}
      </div>
    </header>
  );
}

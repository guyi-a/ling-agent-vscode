import { HistoryIcon, PlusIcon, SettingsIcon } from "./icons";

interface HeaderProps {
  title: string;
  isNew: boolean;
  historyAvailable: boolean;
  settingsDisabled: boolean;
  onHistory(): void;
  onSettings(): void;
  onReset(): void;
}

export function Header({
  title,
  isNew,
  historyAvailable,
  settingsDisabled,
  onHistory,
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

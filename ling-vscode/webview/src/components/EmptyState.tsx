import { ChevronIcon, LingMarkIcon } from "./icons";

const QUICK_PROMPTS = [
  {
    label: "Explain this project",
    prompt: "Explain this project's structure and main components.",
  },
  {
    label: "Review my current changes",
    prompt: "Review my current uncommitted changes and identify issues.",
  },
  {
    label: "Fix a failing test",
    prompt: "Inspect the workspace, identify a failing test, and fix it.",
  },
];

interface EmptyStateProps {
  onPrompt(prompt: string): void;
}

export function EmptyState({ onPrompt }: EmptyStateProps) {
  return (
    <section className="empty-state" aria-label="Start a conversation">
      <div className="empty-state-inner">
        <span className="empty-mark">
          <LingMarkIcon />
        </span>

        <h2>What should we work on?</h2>
        <p>Understand the project, make a change, or run a command.</p>

        <div className="quick-prompts" aria-label="Suggested tasks">
          {QUICK_PROMPTS.map((item) => (
            <button
              className="quick-prompt"
              type="button"
              key={item.label}
              onClick={() => onPrompt(item.prompt)}
            >
              <span>{item.label}</span>
              <ChevronIcon />
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

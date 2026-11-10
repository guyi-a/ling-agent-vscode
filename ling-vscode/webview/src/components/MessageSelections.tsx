import type { UserSelectionSummary } from "../types";

interface MessageSelectionsProps {
  selections: UserSelectionSummary[];
  onOpen(selection: UserSelectionSummary): void;
}

export function MessageSelections({
  selections,
  onOpen,
}: MessageSelectionsProps) {
  if (selections.length === 0) {
    return null;
  }

  return (
    <div className="message-selections">
      {selections.map((selection, index) => {
        const clickable =
          selection.kind === "editor" && Boolean(selection.path);
        return (
          <button
            className={`message-selection-chip${
              clickable ? " message-selection-chip--link" : ""
            }`}
            type="button"
            key={`${selection.label}-${selection.startLine ?? index}-${index}`}
            disabled={!clickable}
            title={
              selection.path
                ? `${selection.path}${
                    selection.startLine
                      ? `:${selection.startLine}-${selection.endLine ?? selection.startLine}`
                      : ""
                  }`
                : selection.label
            }
            onClick={() => {
              if (clickable) {
                onOpen(selection);
              }
            }}
          >
            {selection.lineCount}{" "}
            {selection.lineCount === 1 ? "line" : "lines"} selected
            <span>{selection.label}</span>
          </button>
        );
      })}
    </div>
  );
}

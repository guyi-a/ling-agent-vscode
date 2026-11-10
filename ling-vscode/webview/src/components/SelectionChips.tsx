import type { UserSelectionView } from "../types";
import { CloseIcon } from "./icons";

interface SelectionChipsProps {
  selections: UserSelectionView[];
  onRemove(id: string): void;
}

export function SelectionChips({
  selections,
  onRemove,
}: SelectionChipsProps) {
  if (selections.length === 0) {
    return null;
  }

  return (
    <div className="selection-chips">
      {selections.map((selection) => (
        <div
          className="selection-chip"
          key={selection.id}
          title={selection.path ?? selection.label}
        >
          <span className="selection-chip-count">
            {selection.lineCount}{" "}
            {selection.lineCount === 1 ? "line" : "lines"} selected
          </span>
          <span className="selection-chip-source">{selection.label}</span>
          <button
            className="selection-chip-remove"
            type="button"
            aria-label="Remove selection"
            onClick={() => onRemove(selection.id)}
          >
            <CloseIcon />
          </button>
        </div>
      ))}
    </div>
  );
}

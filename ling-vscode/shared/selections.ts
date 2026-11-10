import type {
  UserSelectionRef,
  UserSelectionView,
} from "./uiTypes";

export function toSelectionRefs(
  selections: readonly UserSelectionView[],
): UserSelectionRef[] {
  return selections.map(
    ({ kind, label, text, lineCount, path, startLine, endLine }) => ({
      kind,
      label,
      text,
      lineCount,
      ...(path ? { path } : {}),
      ...(startLine !== undefined ? { startLine } : {}),
      ...(endLine !== undefined ? { endLine } : {}),
    }),
  );
}

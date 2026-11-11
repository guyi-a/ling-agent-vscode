import * as vscode from "vscode";

export async function ensureInlineDiffView(
  output?: (line: string) => void,
): Promise<void> {
  const ling = vscode.workspace.getConfiguration("ling");
  if (!ling.get<boolean>("diff.inlineView", true)) {
    return;
  }
  const diff = vscode.workspace.getConfiguration("diffEditor");
  if (diff.get<boolean>("renderSideBySide", true) === false) {
    return;
  }

  try {
    await vscode.commands.executeCommand(
      "toggle.diff.renderSideBySide",
    );
    output?.("Switched diff editor to inline view");
  } catch (error) {
    output?.(
      `Unable to switch diff view: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}

import * as vscode from "vscode";

export class PlanCodeLensProvider implements vscode.CodeLensProvider {
  provideCodeLenses(
    document: vscode.TextDocument,
  ): vscode.CodeLens[] {
    if (!isPlanFile(document.uri.fsPath)) {
      return [];
    }
    const range = new vscode.Range(0, 0, 0, 0);
    return [
      new vscode.CodeLens(range, {
        title: "Build plan…",
        command: "ling.buildPlan",
        arguments: [document.uri],
      }),
      new vscode.CodeLens(range, {
        title: "Open Ling chat",
        command: "ling.openChat",
      }),
    ];
  }
}

function isPlanFile(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, "/");
  return (
    normalized.endsWith(".md") &&
    normalized.includes("/.agents/plans/")
  );
}

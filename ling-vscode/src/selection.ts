import { randomUUID } from "node:crypto";
import path from "node:path";
import * as vscode from "vscode";
import type {
  UserSelectionRef,
  UserSelectionView,
} from "../shared/uiTypes";

const MAX_SELECTION_CHARS = 50_000;
const TERMINAL_COPY_DELAY_MS = 80;

export function captureEditorSelection(): UserSelectionRef | undefined {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) {
    return undefined;
  }

  const text = editor.document.getText(editor.selection);
  if (!text.trim()) {
    return undefined;
  }

  const startLine = editor.selection.start.line + 1;
  let endLine = editor.selection.end.line + 1;
  if (editor.selection.end.character === 0 && endLine > startLine) {
    endLine -= 1;
  }
  const label =
    editor.document.uri.scheme === "file"
      ? path.basename(editor.document.fileName)
      : editor.document.fileName || "Untitled";

  return {
    kind: "editor",
    label,
    text: limitSelectionText(text),
    lineCount: Math.max(1, endLine - startLine + 1),
    ...(editor.document.uri.scheme === "file"
      ? { path: editor.document.uri.fsPath }
      : {}),
    startLine,
    endLine,
  };
}

export async function captureTerminalSelection(): Promise<
  UserSelectionRef | undefined
> {
  const terminal = vscode.window.activeTerminal;
  if (!terminal) {
    return undefined;
  }

  let previous = "";
  try {
    previous = await vscode.env.clipboard.readText();
    await vscode.commands.executeCommand(
      "workbench.action.terminal.copySelection",
    );
    await delay(TERMINAL_COPY_DELAY_MS);
    const copied = await vscode.env.clipboard.readText();
    if (!copied.trim() || copied === previous) {
      return undefined;
    }
    await vscode.env.clipboard.writeText(previous);
    return buildTextSelection("terminal", terminal.name, copied);
  } catch {
    return undefined;
  }
}

export function toSelectionView(
  selection: UserSelectionRef,
): UserSelectionView {
  return {
    id: `selection-${randomUUID()}`,
    ...selection,
  };
}

function buildTextSelection(
  kind: "terminal" | "chat",
  label: string,
  text: string,
): UserSelectionRef {
  return {
    kind,
    label,
    text: limitSelectionText(text),
    lineCount: countLines(text),
  };
}

function limitSelectionText(text: string): string {
  if (text.length <= MAX_SELECTION_CHARS) {
    return text;
  }
  return text.slice(0, MAX_SELECTION_CHARS) + "\n[truncated]";
}

function countLines(text: string): number {
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  while (lines.length > 1 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return Math.max(1, lines.length);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

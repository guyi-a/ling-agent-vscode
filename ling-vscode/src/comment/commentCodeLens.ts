import * as vscode from "vscode";
import {
  isJsLanguage,
  isPythonLanguage,
  isSupportedCommentLanguage,
  leadingJsDocStart,
} from "./language";

const DEF_PATTERN =
  /^(\s*)(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)/;
const CLASS_PATTERN = /^(\s*)class\s+([A-Za-z_][A-Za-z0-9_]*)/;

export class CommentCodeLensProvider
  implements vscode.CodeLensProvider, vscode.Disposable
{
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this.changed.event;

  provideCodeLenses(
    document: vscode.TextDocument,
  ): vscode.CodeLens[] | Thenable<vscode.CodeLens[]> {
    if (
      !isSupportedCommentLanguage(document.languageId) ||
      document.uri.scheme !== "file"
    ) {
      return [];
    }
    return this.build(document);
  }

  dispose(): void {
    this.changed.dispose();
  }

  private async build(
    document: vscode.TextDocument,
  ): Promise<vscode.CodeLens[]> {
    const fromLanguageServer = await this.fromLanguageServer(
      document,
    );
    if (fromLanguageServer.length > 0) {
      return fromLanguageServer;
    }
    return isPythonLanguage(document.languageId)
      ? fromText(document)
      : [];
  }

  private async fromLanguageServer(
    document: vscode.TextDocument,
  ): Promise<vscode.CodeLens[]> {
    let symbols: unknown;
    try {
      symbols = await vscode.commands.executeCommand(
        "vscode.executeDocumentSymbolProvider",
        document.uri,
      );
    } catch {
      return [];
    }
    if (!Array.isArray(symbols)) {
      return [];
    }

    const lenses: vscode.CodeLens[] = [];
    visitSymbols(symbols, document, lenses);
    return lenses;
  }
}

function visitSymbols(
  nodes: readonly unknown[],
  document: vscode.TextDocument,
  lenses: vscode.CodeLens[],
): void {
  for (const raw of nodes) {
    const node = raw as {
      kind?: vscode.SymbolKind;
      range?: vscode.Range;
      selectionRange?: vscode.Range;
      location?: { range: vscode.Range };
      children?: unknown[];
    };
    const range = node.range ?? node.location?.range;
    if (!range || typeof node.kind !== "number") {
      continue;
    }

    if (
      isCommentKind(
        node.kind,
        document.lineAt(range.start.line).text,
      )
    ) {
      const line =
        node.selectionRange?.start.line ?? range.start.line;
      const lensLine = isJsLanguage(document.languageId)
        ? (leadingJsDocStart(document, range.start.line) ??
          range.start.line)
        : range.start.line;
      lenses.push(lensAt(document, lensLine, line));
    }

    if (Array.isArray(node.children)) {
      visitSymbols(node.children, document, lenses);
    }
  }
}

function fromText(
  document: vscode.TextDocument,
): vscode.CodeLens[] {
  const lenses: vscode.CodeLens[] = [];
  for (let line = 0; line < document.lineCount; line++) {
    const text = document.lineAt(line).text;
    if (!DEF_PATTERN.test(text) && !CLASS_PATTERN.test(text)) {
      continue;
    }
    lenses.push(lensAt(document, decoratorStart(document, line), line));
  }
  return lenses;
}

function lensAt(
  document: vscode.TextDocument,
  startLine: number,
  targetLine: number,
): vscode.CodeLens {
  return new vscode.CodeLens(
    new vscode.Range(startLine, 0, startLine, 0),
    {
      title: "Generate comment",
      command: "ling.generateComment",
      arguments: [
        {
          uri: document.uri.toString(),
          line: targetLine,
        },
      ],
    },
  );
}

function decoratorStart(
  document: vscode.TextDocument,
  defLine: number,
): number {
  const indent = leadingSpaces(document.lineAt(defLine).text);
  let start = defLine;
  while (start > 0) {
    const previous = document.lineAt(start - 1).text;
    if (
      previous.trimStart().startsWith("@") &&
      leadingSpaces(previous) === indent
    ) {
      start -= 1;
    } else {
      break;
    }
  }
  return start;
}

function leadingSpaces(text: string): number {
  return text.length - text.trimStart().length;
}

function isCommentKind(
  kind: vscode.SymbolKind,
  text: string,
): boolean {
  if (
    kind === vscode.SymbolKind.Function ||
    kind === vscode.SymbolKind.Method ||
    kind === vscode.SymbolKind.Constructor ||
    kind === vscode.SymbolKind.Class
  ) {
    return true;
  }
  if (
    kind === vscode.SymbolKind.Variable ||
    kind === vscode.SymbolKind.Constant
  ) {
    return /=>|\bfunction\b/.test(text);
  }
  return false;
}

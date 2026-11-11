import * as vscode from "vscode";

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
      document.languageId !== "python" ||
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
    return fromLanguageServer.length > 0
      ? fromLanguageServer
      : fromText(document);
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

    if (isCommentKind(node.kind)) {
      const line =
        node.selectionRange?.start.line ?? range.start.line;
      if (!hasDocstring(document, line)) {
        lenses.push(lensAt(document, range.start.line, line));
      }
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
    if (hasDocstring(document, line)) {
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

function hasDocstring(
  document: vscode.TextDocument,
  defLine: number,
): boolean {
  const indent = leadingSpaces(document.lineAt(defLine).text);
  for (let line = defLine + 1; line < document.lineCount; line++) {
    const text = document.lineAt(line).text;
    if (!text.trim()) {
      continue;
    }
    if (leadingSpaces(text) <= indent) {
      return false;
    }
    const trimmed = text.trim();
    return trimmed.startsWith('"""') || trimmed.startsWith("'''");
  }
  return false;
}

function leadingSpaces(text: string): number {
  return text.length - text.trimStart().length;
}

function isCommentKind(kind: vscode.SymbolKind): boolean {
  return (
    kind === vscode.SymbolKind.Function ||
    kind === vscode.SymbolKind.Method ||
    kind === vscode.SymbolKind.Constructor ||
    kind === vscode.SymbolKind.Class
  );
}

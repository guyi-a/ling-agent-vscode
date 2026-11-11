import { createHash } from "node:crypto";
import * as vscode from "vscode";
import type {
  CommentSymbolKind,
  CommentTarget,
} from "../../shared/commentTypes";

const SUPPORTED_LANGUAGES = new Set(["python"]);

export class CommentTargetError extends Error {}

export interface CollectedCommentTarget {
  document: vscode.TextDocument;
  target: CommentTarget;
}

interface SymbolMatch {
  name: string;
  kind: CommentSymbolKind;
  range: vscode.Range;
  parentClassName?: string;
}

export async function collectCommentTarget(
  document: vscode.TextDocument,
  position: vscode.Position,
): Promise<CollectedCommentTarget> {
  if (!SUPPORTED_LANGUAGES.has(document.languageId)) {
    throw new CommentTargetError(
      "Comment generation supports Python only in this version.",
    );
  }
  if (document.uri.scheme !== "file") {
    throw new CommentTargetError(
      "Save the file before generating a comment.",
    );
  }

  const symbol = await findSymbolAt(document, position);
  if (!symbol) {
    throw new CommentTargetError(
      "Place the cursor inside a function, method, or class.",
    );
  }

  const startLine = symbol.range.start.line;
  const endLine = symbol.range.end.line;
  const sourceRange = new vscode.Range(
    startLine,
    0,
    endLine,
    document.lineAt(endLine).text.length,
  );
  const sourceText = document.getText(sourceRange);
  if (!sourceText.trim()) {
    throw new CommentTargetError("The selected symbol is empty.");
  }

  return {
    document,
    target: {
      path: document.uri.fsPath,
      languageId: document.languageId,
      symbolName: symbol.name,
      symbolKind: symbol.kind,
      startLine,
      sourceText,
      documentVersion: document.version,
      contentHash: createHash("sha256")
        .update(sourceText)
        .digest("hex"),
      ...(symbol.parentClassName
        ? { classContext: symbol.parentClassName }
        : {}),
    },
  };
}

async function findSymbolAt(
  document: vscode.TextDocument,
  position: vscode.Position,
): Promise<SymbolMatch | undefined> {
  const fromLanguageServer = await findSymbolFromLanguageServer(
    document,
    position,
  );
  return (
    fromLanguageServer ?? findSymbolFromText(document, position)
  );
}

async function findSymbolFromLanguageServer(
  document: vscode.TextDocument,
  position: vscode.Position,
): Promise<SymbolMatch | undefined> {
  let raw: unknown;
  try {
    raw = await vscode.commands.executeCommand(
      "vscode.executeDocumentSymbolProvider",
      document.uri,
    );
  } catch {
    return undefined;
  }
  if (!Array.isArray(raw) || raw.length === 0) {
    return undefined;
  }

  const matches: SymbolMatch[] = [];
  visitSymbols(raw, position, undefined, matches);
  if (matches.length === 0) {
    return undefined;
  }
  matches.sort(
    (left, right) => rangeSize(left.range) - rangeSize(right.range),
  );
  return matches[0];
}

function rangeSize(range: vscode.Range): number {
  return range.end.line - range.start.line;
}

function visitSymbols(
  nodes: readonly unknown[],
  position: vscode.Position,
  parentClassName: string | undefined,
  matches: SymbolMatch[],
): void {
  for (const raw of nodes) {
    const node = raw as {
      name?: string;
      kind?: vscode.SymbolKind;
      range?: vscode.Range;
      location?: { range: vscode.Range };
      children?: unknown[];
    };
    const range = node.range ?? node.location?.range;
    if (
      !range ||
      typeof node.kind !== "number" ||
      !range.contains(position)
    ) {
      continue;
    }

    const kind = toCommentKind(node.kind);
    if (kind && node.name) {
      matches.push({
        name: node.name,
        kind,
        range,
        ...(parentClassName ? { parentClassName } : {}),
      });
    }

    if (Array.isArray(node.children)) {
      visitSymbols(
        node.children,
        position,
        node.kind === vscode.SymbolKind.Class
          ? (node.name ?? parentClassName)
          : parentClassName,
        matches,
      );
    }
  }
}

function toCommentKind(
  kind: vscode.SymbolKind,
): CommentSymbolKind | undefined {
  switch (kind) {
    case vscode.SymbolKind.Function:
      return "function";
    case vscode.SymbolKind.Method:
    case vscode.SymbolKind.Constructor:
      return "method";
    case vscode.SymbolKind.Class:
      return "class";
    default:
      return undefined;
  }
}

const DEF_PATTERN =
  /^(\s*)(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)/;
const CLASS_PATTERN = /^(\s*)class\s+([A-Za-z_][A-Za-z0-9_]*)/;

function findSymbolFromText(
  document: vscode.TextDocument,
  position: vscode.Position,
): SymbolMatch | undefined {
  for (let line = position.line; line >= 0; line--) {
    const text = document.lineAt(line).text;
    const definition = DEF_PATTERN.exec(text);
    const classDefinition = CLASS_PATTERN.exec(text);
    const match = definition ?? classDefinition;
    if (!match) {
      continue;
    }

    const indent = match[1].length;
    const end = findBlockEnd(document, line, indent);
    if (position.line > end) {
      continue;
    }

    let start = line;
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

    const parentClassName = classDefinition
      ? undefined
      : findEnclosingClass(document, start, indent);
    return {
      name: match[2],
      kind: classDefinition
        ? "class"
        : parentClassName
          ? "method"
          : "function",
      range: new vscode.Range(
        start,
        0,
        end,
        document.lineAt(end).text.length,
      ),
      ...(parentClassName ? { parentClassName } : {}),
    };
  }
  return undefined;
}

function findBlockEnd(
  document: vscode.TextDocument,
  startLine: number,
  indent: number,
): number {
  let end = startLine;
  for (let line = startLine + 1; line < document.lineCount; line++) {
    const text = document.lineAt(line).text;
    if (!text.trim()) {
      continue;
    }
    if (leadingSpaces(text) <= indent) {
      break;
    }
    end = line;
  }
  return end;
}

function findEnclosingClass(
  document: vscode.TextDocument,
  defLine: number,
  defIndent: number,
): string | undefined {
  for (let line = defLine - 1; line >= 0; line--) {
    const text = document.lineAt(line).text;
    const classMatch = CLASS_PATTERN.exec(text);
    if (classMatch && classMatch[1].length < defIndent) {
      return classMatch[2];
    }
    const defMatch = DEF_PATTERN.exec(text);
    if (defMatch && defMatch[1].length < defIndent) {
      return undefined;
    }
  }
  return undefined;
}

function leadingSpaces(text: string): number {
  return text.length - text.trimStart().length;
}

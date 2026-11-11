export type CommentSymbolKind = "function" | "method" | "class";

export interface CommentTarget {
  path: string;
  languageId: string;
  symbolName: string;
  symbolKind: CommentSymbolKind;
  startLine: number;
  sourceText: string;
  documentVersion: number;
  contentHash: string;
  classContext?: string;
}

export interface CommentPlacement {
  line: number;
  indent: string;
}

export interface CommentGeneration {
  comment: string;
  placement: CommentPlacement;
}

export function isCommentGeneration(
  value: unknown,
): value is CommentGeneration {
  if (!isRecord(value) || !isRecord(value.placement)) {
    return false;
  }
  return (
    typeof value.comment === "string" &&
    value.comment.length > 0 &&
    typeof value.placement.line === "number" &&
    Number.isInteger(value.placement.line) &&
    value.placement.line >= 0 &&
    typeof value.placement.indent === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

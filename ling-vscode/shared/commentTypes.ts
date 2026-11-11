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
  endLine?: number;
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
  const placement = value.placement;
  if (
    typeof value.comment !== "string" ||
    value.comment.length === 0 ||
    typeof placement.line !== "number" ||
    !Number.isInteger(placement.line) ||
    placement.line < 0 ||
    typeof placement.indent !== "string"
  ) {
    return false;
  }
  if (placement.endLine === undefined) {
    return true;
  }
  return (
    typeof placement.endLine === "number" &&
    Number.isInteger(placement.endLine) &&
    placement.endLine > placement.line
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

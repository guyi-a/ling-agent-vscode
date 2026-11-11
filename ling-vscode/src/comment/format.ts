import type { CommentPlacement } from "../../shared/commentTypes";

export function formatCommentBlock(
  comment: string,
  indent: string,
  eol: string,
): string {
  const lines = comment
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n");
  const block = lines
    .map((line) => (line.length > 0 ? `${indent}${line}` : ""))
    .join(eol);
  return `${block}${eol}`;
}

export function applyComment(
  text: string,
  placement: CommentPlacement,
  block: string,
): string {
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const blockLines = block.split(/\r?\n/);
  if (blockLines.at(-1) === "") {
    blockLines.pop();
  }
  const remove =
    placement.endLine !== undefined
      ? placement.endLine - placement.line
      : 0;
  lines.splice(placement.line, remove, ...blockLines);
  return lines.join(eol);
}

export function insertAtLine(
  text: string,
  line: number,
  block: string,
): string {
  return applyComment(text, { line, indent: "" }, block);
}

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

export function insertAtLine(
  text: string,
  line: number,
  block: string,
): string {
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const blockLines = block.split(/\r?\n/);
  if (blockLines.at(-1) === "") {
    blockLines.pop();
  }
  lines.splice(line, 0, ...blockLines);
  return lines.join(eol);
}

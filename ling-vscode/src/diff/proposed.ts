import path from "node:path";

export function resolveProposedPath(
  workspaceRoot: string,
  raw: unknown,
): string | undefined {
  if (typeof raw !== "string" || !raw.trim()) {
    return undefined;
  }
  if (path.isAbsolute(raw)) {
    return undefined;
  }

  const resolved = path.resolve(workspaceRoot, raw);
  const relative = path.relative(workspaceRoot, resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    return undefined;
  }
  return resolved;
}

export function computeProposedContent(
  tool: string,
  args: Record<string, unknown>,
  current: string,
): string | undefined {
  if (tool === "write_file") {
    return typeof args.content === "string" ? args.content : undefined;
  }

  if (tool === "edit_file") {
    const oldText = args.old_text;
    const newText = args.new_text;
    if (typeof oldText !== "string" || typeof newText !== "string") {
      return undefined;
    }
    const index = current.indexOf(oldText);
    if (index === -1) {
      return undefined;
    }
    return (
      current.slice(0, index) +
      newText +
      current.slice(index + oldText.length)
    );
  }

  return undefined;
}

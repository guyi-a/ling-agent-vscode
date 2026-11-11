import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * Persistent workspace used when VS Code has no folder open. It keeps chat,
 * attachments, and history in one stable location so the agent can still use
 * its normal tools.
 */
export function resolveScratchWorkspaceRoot(): string {
  const base =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA ??
        process.env.APPDATA ??
        join(homedir(), "AppData", "Local")
      : process.env.XDG_STATE_HOME ??
        join(homedir(), ".local", "state");
  const root = join(base, "ling-agent", "scratch");
  mkdirSync(root, { recursive: true });
  return root;
}

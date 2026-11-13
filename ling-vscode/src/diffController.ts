import path from "node:path";
import { TextDecoder } from "node:util";
import * as vscode from "vscode";
import type {
  ApprovalRequest,
  SessionSnapshot,
} from "../shared/uiTypes";
import {
  computeProposedContent,
  resolveProposedPath,
} from "./diff/proposed";
import { ensureInlineDiffView } from "./diff/view";

const DIFF_SCHEME = "ling-diff";
const MAX_DIFF_BYTES = 1_000_000;
const MAX_TEXT_CHARS = 500_000;

type ApprovalResolver = (
  approvalId: string,
  effectDigest: string,
  approved: boolean,
) => Promise<void>;

interface ActiveDiff {
  approval: ApprovalRequest;
  fileUri: vscode.Uri;
  originalUri: vscode.Uri;
  modifiedUri: vscode.Uri;
  originalVirtual: boolean;
}

export interface ChangeReviewItem {
  path: string;
  beforeExists: boolean;
  before: string | null;
  afterExists: boolean;
  after: string | null;
  added: number;
  removed: number;
}

class DiffContentProvider implements vscode.TextDocumentContentProvider {
  private readonly contents = new Map<string, string>();
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;

  set(uri: vscode.Uri, content: string): void {
    this.contents.set(uri.toString(), content);
    this.changed.fire(uri);
  }

  delete(uri: vscode.Uri): void {
    this.contents.delete(uri.toString());
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.toString()) ?? "";
  }

  dispose(): void {
    this.contents.clear();
    this.changed.dispose();
  }
}

export class DiffController implements vscode.Disposable {
  private readonly provider = new DiffContentProvider();
  private readonly registration: vscode.Disposable;
  private readonly tabListener: vscode.Disposable;
  private active: ActiveDiff | undefined;
  private chain: Promise<void> = Promise.resolve();
  private resolving = false;
  private readonly dismissed = new Set<string>();

  constructor(
    private readonly resolveApproval: ApprovalResolver,
    private readonly output?: (line: string) => void,
  ) {
    this.registration =
      vscode.workspace.registerTextDocumentContentProvider(
        DIFF_SCHEME,
        this.provider,
      );
    this.tabListener = vscode.window.tabGroups.onDidChangeTabs(() => {
      const active = this.active;
      if (active && !this.isDiffOpen(active.modifiedUri)) {
        this.dismissed.add(active.approval.id);
        void this.closeActive(true);
      }
    });
    this.setContext(false);
  }

  update(snapshot: SessionSnapshot): void {
    this.chain = this.chain
      .then(() => this.apply(snapshot))
      .catch((error) => {
        this.output?.(`Unable to open Ling diff: ${formatError(error)}`);
      });
  }

  dispose(): void {
    void this.closeActive();
    this.tabListener.dispose();
    this.registration.dispose();
    this.provider.dispose();
  }

  async resolveActive(approved: boolean): Promise<void> {
    const active = this.active;
    if (!active || this.resolving) {
      return;
    }
    this.resolving = true;
    try {
      await this.resolveApproval(
        active.approval.id,
        active.approval.effectDigest,
        approved,
      );
    } catch (error) {
      this.output?.(
        `Unable to resolve diff approval: ${formatError(error)}`,
      );
      void vscode.window.showErrorMessage(
        `Unable to resolve approval: ${formatError(error)}`,
      );
    } finally {
      this.resolving = false;
    }
  }

  async openChanges(
    runId: string,
    title: string,
    changes: ChangeReviewItem[],
  ): Promise<void> {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root || changes.length === 0) {
      return;
    }
    const token = runId.replace(/[^A-Za-z0-9_-]/g, "_");
    const resources: Array<
      [vscode.Uri, vscode.Uri, vscode.Uri]
    > = [];
    for (const [index, change] of changes.entries()) {
      const filePath = path.resolve(root, change.path);
      const relative = path.relative(root, filePath);
      if (relative.startsWith("..") || path.isAbsolute(relative)) {
        throw new Error(
          `Change path escapes the workspace: ${change.path}`,
        );
      }
      const resource = vscode.Uri.file(filePath);
      const before = this.virtualUri(
        `${token}/${index}`,
        "original",
        path.basename(change.path),
      );
      const after = this.virtualUri(
        `${token}/${index}`,
        "modified",
        path.basename(change.path),
      );
      this.provider.set(before, change.before ?? "");
      this.provider.set(after, change.after ?? "");
      resources.push([resource, before, after]);
    }
    await vscode.commands.executeCommand(
      "vscode.changes",
      title,
      resources,
    );
    await ensureInlineDiffView(this.output);
  }

  private async apply(snapshot: SessionSnapshot): Promise<void> {
    const approval = snapshot.pendingApprovals.find(
      (item) =>
        isEditableApproval(item) && !this.dismissed.has(item.id),
    );
    if (!approval || !isEditableApproval(approval)) {
      await this.closeActive(true);
      return;
    }
    if (this.active?.approval.id === approval.id) {
      return;
    }

    await this.closeActive();
    await this.open(approval);
  }

  private async open(
    approval: ApprovalRequest & { arguments: Record<string, unknown> },
  ): Promise<void> {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) {
      return;
    }
    const filePath = resolveProposedPath(
      root,
      approval.arguments.path,
    );
    if (!filePath) {
      return;
    }

    const fileUri = vscode.Uri.file(filePath);
    let current = "";
    let exists = false;
    try {
      const stat = await vscode.workspace.fs.stat(fileUri);
      if (stat.size > MAX_DIFF_BYTES) {
        return;
      }
      const bytes = await vscode.workspace.fs.readFile(fileUri);
      if (bytes.subarray(0, 8192).includes(0)) {
        return;
      }
      current = new TextDecoder("utf-8").decode(bytes);
      exists = true;
    } catch {
      current = "";
    }

    const proposed = computeProposedContent(
      approval.tool,
      approval.arguments,
      current,
    );
    if (
      proposed === undefined ||
      proposed === current ||
      proposed.length > MAX_TEXT_CHARS
    ) {
      return;
    }

    const relative = path
      .relative(root, filePath)
      .split(path.sep)
      .join("/");
    const base = path.basename(filePath);
    const token = approval.id.replace(/[^A-Za-z0-9_-]/g, "_");
    const originalUri = exists
      ? fileUri
      : this.virtualUri(token, "original", base);
    const modifiedUri = this.virtualUri(token, "modified", base);
    if (!exists) {
      this.provider.set(originalUri, "");
    }
    this.provider.set(modifiedUri, proposed);

    await vscode.commands.executeCommand(
      "vscode.diff",
      originalUri,
      modifiedUri,
      `Ling: ${relative}`,
      { preview: true },
    );
    this.active = {
      approval,
      fileUri,
      originalUri,
      modifiedUri,
      originalVirtual: !exists,
    };
    this.setContext(true);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await ensureInlineDiffView(this.output);
  }

  private async closeActive(reveal = false): Promise<void> {
    const active = this.active;
    if (!active) {
      this.setContext(false);
      return;
    }
    this.active = undefined;
    this.setContext(false);

    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        if (
          tab.input instanceof vscode.TabInputTextDiff &&
          tab.input.modified.toString() === active.modifiedUri.toString()
        ) {
          await vscode.window.tabGroups.close(tab);
        }
      }
    }

    this.provider.delete(active.modifiedUri);
    if (active.originalVirtual) {
      this.provider.delete(active.originalUri);
    }
    if (reveal) {
      await this.revealDocument(active.fileUri);
    }
  }

  private async revealDocument(uri: vscode.Uri): Promise<void> {
    try {
      await vscode.workspace.fs.stat(uri);
    } catch {
      return;
    }
    const visible = vscode.window.visibleTextEditors.some(
      (editor) => editor.document.uri.toString() === uri.toString(),
    );
    if (visible) {
      return;
    }
    const open = vscode.workspace.textDocuments.find(
      (document) => document.uri.toString() === uri.toString(),
    );
    if (open) {
      await vscode.window.showTextDocument(open, {
        preview: false,
      });
      return;
    }
    try {
      const document = await vscode.workspace.openTextDocument(uri);
      await vscode.window.showTextDocument(document, {
        preview: false,
      });
    } catch (error) {
      this.output?.(
        `Unable to reopen ${uri.fsPath}: ${formatError(error)}`,
      );
    }
  }

  private setContext(active: boolean): void {
    void Promise.resolve(
      vscode.commands.executeCommand(
        "setContext",
        "ling.diffApproval",
        active,
      ),
    ).catch(() => undefined);
  }

  private isDiffOpen(modifiedUri: vscode.Uri): boolean {
    const target = modifiedUri.toString();
    return vscode.window.tabGroups.all.some((group) =>
      group.tabs.some(
        (tab) =>
          tab.input instanceof vscode.TabInputTextDiff &&
          tab.input.modified.toString() === target,
      ),
    );
  }

  private virtualUri(
    token: string,
    side: "original" | "modified",
    name: string,
  ): vscode.Uri {
    return vscode.Uri.from({
      scheme: DIFF_SCHEME,
      path: `/${token}/${side}/${name}`,
    });
  }
}

function isEditableApproval(
  approval: ApprovalRequest,
): approval is ApprovalRequest & {
  arguments: Record<string, unknown>;
} {
  return (
    (approval.tool === "write_file" || approval.tool === "edit_file") &&
    isRecord(approval.arguments) &&
    typeof approval.arguments.path === "string"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

import path from "node:path";
import * as vscode from "vscode";
import type {
  CommentGeneration,
  CommentTarget,
} from "../../shared/commentTypes";
import { ensureInlineDiffView } from "../diff/view";
import { formatCommentBlock, insertAtLine } from "./format";

const SCHEME = "ling-comment";
const CONTEXT_KEY = "ling.commentPreview";

interface ActivePreview {
  documentUri: vscode.Uri;
  documentVersion: number;
  placementLine: number;
  block: string;
  modifiedUri: vscode.Uri;
}

class CommentContentProvider
  implements vscode.TextDocumentContentProvider, vscode.Disposable
{
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

export class CommentPreviewController implements vscode.Disposable {
  private readonly provider = new CommentContentProvider();
  private readonly registration: vscode.Disposable;
  private active: ActivePreview | undefined;

  constructor(private readonly output?: (line: string) => void) {
    this.registration =
      vscode.workspace.registerTextDocumentContentProvider(
        SCHEME,
        this.provider,
      );
    this.setContext(false);
  }

  async show(
    document: vscode.TextDocument,
    target: CommentTarget,
    generation: CommentGeneration,
  ): Promise<void> {
    await this.close();

    if (document.version !== target.documentVersion) {
      throw new Error(
        "The file changed while the comment was being generated. Try again.",
      );
    }

    const line = generation.placement.line;
    if (line < 0 || line > document.lineCount) {
      throw new Error(
        "The generated placement is outside the document.",
      );
    }

    const eol =
      document.eol === vscode.EndOfLine.CRLF ? "\r\n" : "\n";
    const block = formatCommentBlock(
      generation.comment,
      generation.placement.indent,
      eol,
    );
    const modifiedText = insertAtLine(document.getText(), line, block);

    const modifiedUri = vscode.Uri.from({
      scheme: SCHEME,
      path: `/${Date.now().toString(36)}/${path.basename(
        document.uri.fsPath,
      )}`,
    });
    this.provider.set(modifiedUri, modifiedText);

    await vscode.commands.executeCommand(
      "vscode.diff",
      document.uri,
      modifiedUri,
      `Ling comment: ${vscode.workspace.asRelativePath(
        document.uri,
        false,
      )}`,
      { preview: true },
    );

    this.active = {
      documentUri: document.uri,
      documentVersion: document.version,
      placementLine: line,
      block,
      modifiedUri,
    };
    this.setContext(true);
    await new Promise((resolve) => setTimeout(resolve, 60));
    await ensureInlineDiffView(this.output);
  }

  async accept(): Promise<void> {
    const active = this.active;
    if (!active) {
      return;
    }

    const document = vscode.workspace.textDocuments.find(
      (item) =>
        item.uri.toString() === active.documentUri.toString(),
    );
    if (
      !document ||
      document.version !== active.documentVersion
    ) {
      await this.close();
      void vscode.window.showWarningMessage(
        "The file changed while the preview was open. Generate the comment again.",
      );
      return;
    }

    const edit = new vscode.WorkspaceEdit();
    edit.insert(
      document.uri,
      new vscode.Position(active.placementLine, 0),
      active.block,
    );
    const applied = await vscode.workspace.applyEdit(edit);
    if (!applied) {
      throw new Error("Unable to insert the comment.");
    }
    const saved = await document.save();
    await this.close();
    if (!saved) {
      void vscode.window.showWarningMessage(
        "Comment inserted, but the file could not be saved.",
      );
    }
  }

  async reject(): Promise<void> {
    await this.close();
  }

  dispose(): void {
    void this.close();
    this.registration.dispose();
    this.provider.dispose();
  }

  private async close(): Promise<void> {
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
          tab.input.modified.toString() ===
            active.modifiedUri.toString()
        ) {
          await vscode.window.tabGroups.close(tab);
        }
      }
    }
    this.provider.delete(active.modifiedUri);
  }

  private setContext(active: boolean): void {
    void Promise.resolve(
      vscode.commands.executeCommand(
        "setContext",
        CONTEXT_KEY,
        active,
      ),
    ).catch(() => undefined);
  }
}


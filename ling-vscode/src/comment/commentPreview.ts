import path from "node:path";
import * as vscode from "vscode";
import type {
  CommentGeneration,
  CommentPlacement,
  CommentTarget,
} from "../../shared/commentTypes";
import { ensureInlineDiffView } from "../diff/view";
import { applyComment, formatCommentBlock } from "./format";

const SCHEME = "ling-comment";
const CONTEXT_KEY = "ling.commentPreview";

interface ActivePreview {
  documentUri: vscode.Uri;
  documentVersion: number;
  placement: CommentPlacement;
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

    const placement = generation.placement;
    if (placement.line < 0 || placement.line > document.lineCount) {
      throw new Error(
        "The generated placement is outside the document.",
      );
    }
    if (
      placement.endLine !== undefined &&
      (placement.endLine <= placement.line ||
        placement.endLine > document.lineCount)
    ) {
      throw new Error(
        "The generated replacement range is outside the document.",
      );
    }

    const eol =
      document.eol === vscode.EndOfLine.CRLF ? "\r\n" : "\n";
    const block = formatCommentBlock(
      generation.comment,
      placement.indent,
      eol,
    );
    const modifiedText = applyComment(
      document.getText(),
      placement,
      block,
    );

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
      placement,
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
    if (active.placement.endLine !== undefined) {
      edit.replace(
        document.uri,
        new vscode.Range(
          active.placement.line,
          0,
          active.placement.endLine,
          0,
        ),
        active.block,
      );
    } else {
      edit.insert(
        document.uri,
        new vscode.Position(active.placement.line, 0),
        active.block,
      );
    }
    const applied = await vscode.workspace.applyEdit(edit);
    if (!applied) {
      throw new Error("Unable to insert the comment.");
    }
    const saved = await document.save();
    await this.close();
    await this.revealDocument(active.documentUri);
    if (!saved) {
      void vscode.window.showWarningMessage(
        "Comment inserted, but the file could not be saved.",
      );
    }
  }

  async reject(): Promise<void> {
    const active = this.active;
    await this.close();
    if (active) {
      await this.revealDocument(active.documentUri);
    }
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

  private async revealDocument(uri: vscode.Uri): Promise<void> {
    const visible = vscode.window.visibleTextEditors.some(
      (editor) => editor.document.uri.toString() === uri.toString(),
    );
    if (visible) {
      return;
    }
    const document = vscode.workspace.textDocuments.find(
      (item) => item.uri.toString() === uri.toString(),
    );
    if (document) {
      await vscode.window.showTextDocument(document, {
        preview: false,
      });
    }
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

